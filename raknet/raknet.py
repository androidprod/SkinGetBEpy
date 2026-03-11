"""
RakNet.py - RakNet サーバー実装 (並列処理版)
Mirrors: RakNet/RakNet.cpp

並列処理設計:
  - メインスレッド  : UDP 受信のみ (select ループ)
  - パケットキュー  : queue.Queue でスレッドセーフに受け渡し
  - ワーカースレッド : ThreadPoolExecutor でパケット処理を並列化
  - ACK スレッド    : 定期 ACK 送信専用スレッド

0xC1 対応:
  - RakNet の ACK は 0xC0 だが、一部クライアントは 0xC1〜0xCF も送る
  - 0xC0〜0xCF を全て ACK 系として扱う
"""
import time
import threading
import queue
import struct
from concurrent.futures import ThreadPoolExecutor

from util.buffer import Buffer
from util.logger import Logger
from bedrock.packets import (
    RAKNET_MAGIC,
    RAKNET_UNCONNECTED_PING, RAKNET_UNCONNECTED_PING_OPEN,
    RAKNET_UNCONNECTED_PONG,
    RAKNET_OPEN_CONNECTION_REQUEST1, RAKNET_OPEN_CONNECTION_REPLY1,
    RAKNET_OPEN_CONNECTION_REQUEST2, RAKNET_OPEN_CONNECTION_REPLY2,
    RAKNET_CONNECTION_REQUEST, RAKNET_CONNECTION_REQUEST_ACCEPT,
    RAKNET_NEW_INCOMING_CONNECTION,
    RAKNET_CONNECTED_PING, RAKNET_CONNECTED_PONG,
    RAKNET_DISCONNECT,
    RAKNET_FRAME_SET_MIN, RAKNET_FRAME_SET_MAX,
    RAKNET_ACK, RAKNET_NACK,
    GAME_PACKET_WRAPPER,
    PACKET_LOGIN, PACKET_RESOURCE_PACK_CLIENT_RESP,
    PACKET_PLAY_STATUS, PACKET_RESOURCE_PACKS_INFO,
    PACKET_REQUEST_NETWORK_SETTINGS,
    PLAY_STATUS_LOGIN_SUCCESS, PLAY_STATUS_PLAYER_SPAWN,
    RESOURCE_PACK_RESPONSE_COMPLETED,
    RAKNET_FRAME_SET_C1,
)
from raknet.reliability import (
    parse_frame_set, build_frame, build_frame_set,
    build_ack, build_nack, SplitPacketAssembler,
    RELIABLE_ORDERED,
)
from bedrock.login import (
    unwrap_game_packet, read_packets, parse_login_packet,
    make_play_status, make_resource_packs_info, make_resource_pack_stack,
    make_network_settings, make_disconnect, make_sub_packet, wrap_game_packet,
    _make_play_status, _make_disconnect,
)

log = Logger("RakNet")

DEFAULT_MTU = 1400

MOTD_TEMPLATE = (
    "MCPE;SkinGetBE;{protocol};{version};0;100;"
    "{guid};SkinGetBE;Creative;1;{port};{port};"
)


class ConnectionState:
    OFFLINE     = 0
    CONNECTING  = 1
    HANDSHAKING = 2
    CONNECTED   = 3
    LOGGED_IN   = 4


class ClientSession:
    def __init__(self, addr: tuple, mtu: int, guid: int):
        self.addr  = addr
        self.mtu   = mtu
        self.guid  = guid
        self.state = ConnectionState.CONNECTING
        self.split_asm = SplitPacketAssembler()

        self.send_seq_num   = 0
        self.send_msg_idx   = 0
        self.send_order_idx = 0

        self.ack_queue: list = []
        self.skin_saved = False

        # セッションごとのロック (送信カウンター保護)
        self.lock = threading.Lock()


class RakNetServer:
    """
    RakNet サーバー (並列処理版)

    スレッド構成:
      Thread-Main   : UDP 受信 → packet_queue に投入
      Thread-Worker : ThreadPoolExecutor でパケット処理
      Thread-ACK    : 100ms 間隔で ACK を一括送信
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 19132,
                 server_guid: int = None, on_skin_received=None,
                 protocol: int = 776, game_version: str = "1.21.0",
                 worker_threads: int = 4):
        self.host         = host
        self.port         = port
        self.server_guid  = 0x1234567812345678 # C++版固定 GUID
        self.on_skin_received = on_skin_received
        self.protocol     = protocol
        self.game_version = game_version
        self.worker_threads = worker_threads

        self._sessions: dict = {}          # addr → ClientSession
        self._sessions_lock = threading.RLock()

        self._socket = None
        self._running = False

        # パケットキュー: (data, addr) のタプルを投入
        self._packet_queue: queue.Queue = queue.Queue(maxsize=4096)

        self._executor: ThreadPoolExecutor | None = None
        self._ack_thread: threading.Thread | None = None

    # ────────────────────────────────────────────────────────
    #  公開 API
    # ────────────────────────────────────────────────────────

    def start(self):
        import socket
        import select

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._running = True

        # NAT Discovery (STUN) - 起動を妨げないよう別スレッドで実行
        # サーバーソケットを共有するとエラーの原因になるため、内部で新規ソケット作成
        from util.stun import discover_external_ip
        threading.Thread(target=discover_external_ip, daemon=True).start()

        # ワーカースレッドプール起動
        self._executor = ThreadPoolExecutor(
            max_workers=self.worker_threads,
            thread_name_prefix="RakWorker",
        )

        # ACK 送信スレッド起動
        self._ack_thread = threading.Thread(
            target=self._ack_loop,
            name="RakACK",
            daemon=True,
        )
        self._ack_thread.start()

        # ワーカーディスパッチスレッド起動
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="RakDispatch",
            daemon=True,
        )
        self._dispatch_thread.start()

        log.ok(
            f"RakNet サーバー起動: {self.host}:{self.port} "
            f"(GUID: {self.server_guid:#x}, workers: {self.worker_threads})"
        )

        # メインスレッド: 受信のみ
        try:
            while self._running:
                try:
                    readable, _, _ = select.select([self._socket], [], [], 0.1)
                    if readable:
                        data, addr = self._socket.recvfrom(65535)
                        try:
                            self._packet_queue.put_nowait((data, addr))
                        except queue.Full:
                            pass
                except (BlockingIOError, InterruptedError):
                    continue
                except ConnectionResetError:
                    # Windows で送信先のポートが閉じている場合に recvfrom が 10054 を吐く不具合への対策
                    continue
                except Exception as e:
                    if self._running:
                        log.debug(f"Recv Error: {e}")
        except KeyboardInterrupt:
            log.info("サーバー停止中...")
        finally:
            self._running = False
            self._executor.shutdown(wait=False)
            try:
                self._socket.close()
            except Exception:
                pass
            log.info("サーバー終了")

    def stop(self):
        self._running = False

    # ────────────────────────────────────────────────────────
    #  スレッド: キューからパケットを取り出してワーカーへ投入
    # ────────────────────────────────────────────────────────

    def _dispatch_loop(self):
        while self._running:
            try:
                data, addr = self._packet_queue.get(timeout=0.5)
                self._executor.submit(self._handle_packet, data, addr)
            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"ディスパッチエラー: {e}")

    # ────────────────────────────────────────────────────────
    #  スレッド: ACK 定期送信 (100ms 間隔)
    # ────────────────────────────────────────────────────────

    def _ack_loop(self):
        while self._running:
            time.sleep(0.1)
            with self._sessions_lock:
                sessions = list(self._sessions.values())
            for session in sessions:
                with session.lock:
                    if session.ack_queue:
                        ack_pkt = build_ack(session.ack_queue)
                        session.ack_queue.clear()
                self._send(ack_pkt, session.addr)

    # ────────────────────────────────────────────────────────
    #  パケット送信
    # ────────────────────────────────────────────────────────

    def _send(self, data: bytes, addr: tuple):
        try:
            self._socket.sendto(data, addr)
        except Exception as e:
            log.error(f"送信失敗 {addr}: {e}")

    def _send_frame(self, session: ClientSession, body: bytes,
                    reliability: int = RELIABLE_ORDERED):
        with session.lock:
            frame_data = build_frame(
                body,
                reliability=reliability,
                reliable_msg_index=session.send_msg_idx,
                order_index=session.send_order_idx,
            )
            pkt = build_frame_set(session.send_seq_num, frame_data)
            session.send_seq_num   = (session.send_seq_num   + 1) & 0xFFFFFF
            session.send_msg_idx   = (session.send_msg_idx   + 1) & 0xFFFFFF
            session.send_order_idx = (session.send_order_idx + 1) & 0xFFFFFF
        self._send(pkt, session.addr)

    # ────────────────────────────────────────────────────────
    #  パケットルーティング (ワーカースレッドで実行)
    # ────────────────────────────────────────────────────────

    def _handle_packet(self, data: bytes, addr: tuple):
        if not data:
            return
        pid = data[0]
        
        # デバッグログ: 受信パケットの ID と長さを表示
        log.debug(f"Packet received: ID=0x{pid:02X}, size={len(data)} from {addr}")

        # ─ オフラインパケット ────────────────────────────────
        if pid in (RAKNET_UNCONNECTED_PING, RAKNET_UNCONNECTED_PING_OPEN):
            self._handle_unconnected_ping(data, addr)

        elif pid == RAKNET_OPEN_CONNECTION_REQUEST1:
            self._handle_open_conn_req1(data, addr)

        elif pid == RAKNET_OPEN_CONNECTION_REQUEST2:
            self._handle_open_conn_req2(data, addr)

        # ─ ACK (exactly 0xC0) ────────────────────────────────
        elif pid == RAKNET_ACK:
            pass  # ACK 受信

        # ─ NACK (exactly 0xA0) ───────────────────────────────
        elif pid == RAKNET_NACK:
            log.warn(f"NACK 受信 from {addr}")

        # ─ Frame Set: bit7 が立っていれば全て Frame Set ────────
        # 標準 (0x80-0x8F) だけでなく、Bedrock が送る 0xC1 等も含む。
        elif (pid & 0x80) or pid == RAKNET_FRAME_SET_C1:
            self._handle_frame_set(data, addr)

        else:
            log.debug(f"未知パケット ID: 0x{pid:02X} from {addr}")

    # ────────────────────────────────────────────────────────
    #  オフラインハンドシェイク
    # ────────────────────────────────────────────────────────

    def _handle_unconnected_ping(self, data: bytes, addr: tuple):
        try:
            buf = Buffer(data)
            buf.read_uint8() # ID
            client_time = buf.read_uint64_be()
        except Exception:
            client_time = int(time.time() * 1000)

        log.debug(f"Handling Unconnected Ping from {addr} (client_time={client_time})")

        motd = MOTD_TEMPLATE.format(
            protocol=self.protocol,
            version=self.game_version,
            guid=str(self.server_guid),
            port=self.port,
        )
        resp = Buffer()
        resp.write_uint8(RAKNET_UNCONNECTED_PONG)
        resp.write_uint64_be(client_time)
        resp.write_uint64_be(self.server_guid)
        resp.write(RAKNET_MAGIC)
        resp.write_string16(motd)
        self._send(resp.get_bytes(), addr)
        log.ok(f"Sent Unconnected Pong to {addr}")

    def _handle_open_conn_req1(self, data: bytes, addr: tuple):
        buf = Buffer(data)
        buf.read_uint8()
        buf.read(16)
        protocol = buf.read_uint8()
        mtu = len(data) + 28
        log.info(f"OpenConnReq1 from {addr} protocol={protocol} mtu={mtu}")

        resp = Buffer()
        resp.write_uint8(RAKNET_OPEN_CONNECTION_REPLY1)
        resp.write(RAKNET_MAGIC)
        resp.write_uint64_be(self.server_guid)
        resp.write_uint8(0) # Security disallowed
        resp.write_uint16_be(1492) # MTU
        self._send(resp.get_bytes(), addr)
        log.info(f"Sent OpenConnectionReply1 to {addr}")

    def _handle_open_conn_req2(self, data: bytes, addr: tuple):
        buf = Buffer(data)
        buf.read_uint8()
        buf.read(16)
        buf.read_address()
        mtu         = buf.read_uint16_be()
        client_guid = buf.read_uint64_be()
        log.info(f"OpenConnReq2 from {addr} mtu={mtu} guid={client_guid:#x}")

        session = ClientSession(addr, mtu, client_guid)
        with self._sessions_lock:
            self._sessions[addr] = session

        resp = Buffer()
        resp.write_uint8(RAKNET_OPEN_CONNECTION_REPLY2)
        resp.write(RAKNET_MAGIC)
        resp.write_uint64_be(self.server_guid)
        resp.write_address(addr[0], addr[1])
        resp.write_uint16_be(1492) # MTU
        resp.write_uint8(0) # Security disallowed
        self._send(resp.get_bytes(), addr)
        log.info(f"Sent OpenConnectionReply2 to {addr}")

    # ────────────────────────────────────────────────────────
    #  Frame Set 処理
    # ────────────────────────────────────────────────────────

    def _handle_frame_set(self, data: bytes, addr: tuple):
        with self._sessions_lock:
            session = self._sessions.get(addr)
        if session is None:
            log.debug(f"Session not found for Frame Set from {addr}")
            return

        try:
            seq_num, frames = parse_frame_set(data)
            log.debug(f"Frame Set received: seq={seq_num}, frames={len(frames)} from {addr}")
        except Exception as e:
            log.warn(f"Frame Set 解析失敗 from {addr}: {e}")
            return

        with session.lock:
            session.ack_queue.append(seq_num)

        for frame in frames:
            body = frame.body
            if frame.has_split:
                body = session.split_asm.add(frame)
                if body is None:
                    continue
            self._handle_frame_body(body, session)

    def _handle_frame_body(self, body: bytes, session: ClientSession):
        if not body:
            return
        pid = body[0]

        if pid == RAKNET_CONNECTED_PING:
            self._handle_connected_ping(body, session)
        elif pid == RAKNET_CONNECTION_REQUEST:
            self._handle_connection_request(body, session)
        elif pid == RAKNET_NEW_INCOMING_CONNECTION:
            log.ok(f"RakNet 接続確立: {session.addr}")
            session.state = ConnectionState.CONNECTED
        elif pid == RAKNET_DISCONNECT:
            log.info(f"クライアント切断: {session.addr}")
            with self._sessions_lock:
                self._sessions.pop(session.addr, None)
        elif pid == GAME_PACKET_WRAPPER:
            log.debug(f"Game Packet Wrapper (0xFE) received from {session.addr}, body_len={len(body)}")
            self._handle_game_packet(body, session)
        else:
            log.debug(f"Frame body unknown ID=0x{pid:02X}, len={len(body)} from {session.addr}")

    # ────────────────────────────────────────────────────────
    #  接続ハンドシェイク
    # ────────────────────────────────────────────────────────

    def _handle_connected_ping(self, body: bytes, session: ClientSession):
        buf = Buffer(body)
        buf.read_uint8()
        ping_time = buf.read_int64_be()

        resp = Buffer()
        resp.write_uint8(RAKNET_CONNECTED_PONG)
        resp.write_int64_be(ping_time)
        resp.write_int64_be(int(time.time() * 1000))
        self._send_frame(session, resp.get_bytes())

    def _handle_connection_request(self, body: bytes, session: ClientSession):
        buf = Buffer(body)
        buf.read_uint8()
        client_guid  = buf.read_uint64_be()
        request_time = buf.read_int64_be()
        log.info(f"Connection Request: guid={client_guid:#x} from {session.addr}")
        session.state = ConnectionState.HANDSHAKING

        now = int(time.time() * 1000)
        resp = Buffer()
        resp.write_uint8(RAKNET_CONNECTION_REQUEST_ACCEPT)
        resp.write_address(session.addr[0], session.addr[1])
        resp.write_uint16_be(0) # Port offset/System index
        # Systemic addresses: 10 addresses of 0.0.0.0 (exact C++ structure)
        for _ in range(10):
            resp.write_uint8(4)     # IPv4
            resp.write_uint32_be(0) # IP 0.0.0.0
            resp.write_uint16_be(0) # Port 0 (Big Endian zero is zero)
        resp.write_int64_be(request_time)
        resp.write_int64_be(0x12345678) # Fixed timestamp as per main.cpp
        self._send_frame(session, resp.get_bytes())
        log.ok(f"Sent ConnectionRequestAccepted to {session.addr}")

    # ────────────────────────────────────────────────────────
    #  Bedrock ゲームパケット
    # ────────────────────────────────────────────────────────

    def _handle_game_packet(self, data: bytes, session: ClientSession):
        try:
            log.debug(f"Game packet raw: 0xFE + {data[1:5].hex() if len(data)>1 else 'empty'}")
            decompressed = unwrap_game_packet(data)
            log.debug(f"Decompressed {len(data)} -> {len(decompressed)} bytes")
            packets = read_packets(decompressed)
            log.debug(f"Parsed {len(packets)} sub-packets")
        except Exception as e:
            hex_preview = data[1:17].hex() if len(data) > 1 else ""
            log.warn(f"ゲームパケット解凍失敗 from {session.addr}: {e} (先頭bytes: {hex_preview})")
            return

        for pkt_data in packets:
            if pkt_data:
                self._dispatch_bedrock_packet(pkt_data, session)

    def _dispatch_bedrock_packet(self, pkt_data: bytes, session: ClientSession):
        try:
            buf    = Buffer(pkt_data)
            pkt_id = buf.read_varint()
        except Exception:
            return

        log.debug(f"Bedrock packet ID=0x{pkt_id:02X} from {session.addr}")

        if pkt_id == PACKET_LOGIN:
            self._handle_login(pkt_data, session)
        elif pkt_id == PACKET_REQUEST_NETWORK_SETTINGS:
            self._handle_network_settings_request(pkt_data, session)
        elif pkt_id == PACKET_RESOURCE_PACK_CLIENT_RESP:
            self._handle_resource_pack_response(pkt_data, session)
        else:
            log.debug(f"未対応パケット ID: 0x{pkt_id:02X} from {session.addr}")

    def _handle_network_settings_request(self, pkt_data: bytes, session: ClientSession):
        log.info(f"Network Settings Request: {session.addr}")
        self._send_frame(session, make_network_settings())

    def _handle_login(self, pkt_data: bytes, session: ClientSession):
        log.info(f"Login パケット受信 from {session.addr}")
        skin_data = parse_login_packet(pkt_data)

        if skin_data:
            session.state = ConnectionState.LOGGED_IN
            log.ok(f"\n{skin_data}")
            if self.on_skin_received:
                # スキン保存は別スレッドで実行
                self._executor.submit(self.on_skin_received, skin_data, session.addr)
            session.skin_saved = True

            # Skin Get 完了後、PlayStatus + Disconnect を一括送信して切断
            # _make_... バージョンは 0xFE ラップ前のサブパケットを返す
            status_pkt = _make_play_status(PLAY_STATUS_LOGIN_SUCCESS)
            disc_pkt   = _make_disconnect("Skin captured successfully!")
            
            # 2つのパケットを1つの 0xFE バッチにまとめる (atomic 送信)
            bundled = wrap_game_packet(status_pkt + disc_pkt, compress=True)
            self._send_frame(session, bundled)
            log.ok(f"Bundled PlayStatus + Disconnect 送信完了: {session.addr}")
        else:
            # スキン取得失敗時など
            self._send_frame(session, make_play_status(PLAY_STATUS_LOGIN_SUCCESS))
            self._send_frame(session, make_resource_packs_info())

    def _handle_resource_pack_response(self, pkt_data: bytes, session: ClientSession):
        try:
            buf    = Buffer(pkt_data)
            buf.read_varint()
            status = buf.read_uint8()
            log.debug(f"ResourcePackClientResponse: status={status}")
        except Exception:
            status = 0

        if status == RESOURCE_PACK_RESPONSE_COMPLETED:
            log.ok(f"ResourcePack 完了 → Spawn: {session.addr}")
            self._send_frame(session, make_play_status(PLAY_STATUS_PLAYER_SPAWN))
        else:
            self._send_frame(session, make_resource_pack_stack())