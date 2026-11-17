import html
import io
import logging
import os
from pathlib import Path
import re
import shlex
import paramiko
from .utils import retry_operation, validate_target

_KNOWN_HOSTS_FILE = Path(__file__).parent.parent / "data" / "ssh_known_hosts"


class _TOFUHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """首次连接记录并持久化公钥指纹，后续连接若指纹变化将由 Paramiko 拒绝连接（防 MitM）。"""

    def __init__(self, known_hosts_path: Path):
        self.known_hosts_path = known_hosts_path

    def missing_host_key(self, client, hostname, key):
        host_keys = client.get_host_keys()
        host_keys.add(hostname, key.get_name(), key)
        try:
            self.known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
            host_keys.save(str(self.known_hosts_path))
            try:
                os.chmod(self.known_hosts_path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            logging.warning(f"保存 SSH known_hosts 失败: {exc}")


def _exec_ssh_command(server_info: dict, cmd: str, cmd_timeout: int = 20) -> tuple:
    """在远程节点执行 SSH 命令，返回 (stdout, stderr, exit_status)。"""
    host = server_info['host']
    port = int(server_info.get('port', 22))
    username = server_info['username']
    password = server_info.get('password')
    private_key_pem = server_info.get('private_key')

    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    if _KNOWN_HOSTS_FILE.exists():
        try:
            ssh.load_host_keys(str(_KNOWN_HOSTS_FILE))
        except Exception as exc:
            logging.warning(f"加载 SSH known_hosts 失败: {exc}")
    ssh.set_missing_host_key_policy(_TOFUHostKeyPolicy(_KNOWN_HOSTS_FILE))

    pkey = None
    if private_key_pem:
        for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = key_cls.from_private_key(io.StringIO(private_key_pem))
                break
            except Exception:
                continue

    try:
        logging.info(f"正在连接到服务器 {host}:{port}")
        ssh.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            timeout=5,
            allow_agent=False,
            look_for_keys=False,
        )
        logging.info(f"正在执行命令: {cmd}")
        _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=cmd_timeout)
        output = stdout.read().decode('utf-8', errors='ignore')
        error = stderr.read().decode('utf-8', errors='ignore')
        exit_status = stdout.channel.recv_exit_status()
        return output, error, exit_status
    except (paramiko.AuthenticationException, paramiko.BadHostKeyException):
        raise
    except Exception as e:
        raise Exception(f"SSH 或执行命令异常: {str(e)}") from e
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def parse_ping_output(output: str) -> str:
    lines = output.strip().split("\n")
    packets_line = None
    rtt_line = None

    for line in lines:
        if "packets transmitted" in line and "packet loss" in line:
            packets_line = line.strip()
        if "rtt min/avg/max/mdev" in line or "round-trip min/avg/max" in line:
            rtt_line = line.strip()

    transmitted = received = packet_loss = None
    min_rtt = avg_rtt = max_rtt = mdev = None

    if packets_line:
        pattern_packets = re.compile(
            r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,\s+([\d\.]+)%\s+packet loss"
        )
        match = pattern_packets.search(packets_line)
        if match:
            transmitted, received, packet_loss = match.groups()

    if rtt_line:
        pattern_rtt = re.compile(r"min/avg/max(?:/mdev)?\s*=\s*([\d\.]+)/([\d\.]+)/([\d\.]+)(?:/([\d\.]+))?\s+ms")
        match = pattern_rtt.search(rtt_line)
        if match:
            min_rtt, avg_rtt, max_rtt, mdev = match.groups()

    if transmitted is not None and received is not None and packet_loss is not None:
        lines_out = [
            f"发出包数: {transmitted}",
            f"收到包数: {received}",
            f"丢包率: {packet_loss}%",
        ]
        if min_rtt is not None and avg_rtt is not None and max_rtt is not None:
            lines_out.extend([
                f"最小延迟: {min_rtt} ms",
                f"平均延迟: {avg_rtt} ms",
                f"最大延迟: {max_rtt} ms",
            ])
            if mdev is not None:
                lines_out.append(f"抖动(mdev): {mdev} ms")
        return "\n".join(lines_out)
    return output

def format_nexttrace_result(raw_output: str, server_name: str, target: str, ip_type: str, trace_mode: str = "icmp") -> str:
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    clean_output = ansi_escape.sub('', raw_output)

    lines = clean_output.splitlines()
    header_lines = []
    hop_lines = []
    map_url_line = None

    in_hops = False
    found_mode = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("MapTrace URL:"):
            map_url_line = stripped
            in_hops = False
            break

        if (
            "ICMP mode" in stripped
            or "UDP mode" in stripped
            or "TCP mode" in stripped
            or "TCP SYN" in stripped
        ):
            found_mode = True
            in_hops = True
            continue

        if in_hops:
            hop_lines.append(stripped)
        else:
            header_lines.append(stripped)

    condensed_hops = []
    current_hop = ""
    hop_start_pattern = re.compile(r'^\d+\s+')
    for hl in hop_lines:
        if not hl:
            continue
        if hop_start_pattern.match(hl):
            if current_hop:
                current_hop = re.sub(r'\s+', ' ', current_hop).strip()
                condensed_hops.append(current_hop)
            current_hop = hl
        else:
            current_hop += " " + hl
    if current_hop:
        current_hop = re.sub(r'\s+', ' ', current_hop).strip()
        condensed_hops.append(current_hop)

    # 对第 1 跳 IP 脱敏（支持 IPv4 与含至少 2 个十六进制段的 IPv6，避免误匹配普通符号）
    if condensed_hops:
        ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        ipv6_pattern = r'(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}|::(?:[0-9A-Fa-f]{1,4}:){0,5}[0-9A-Fa-f]{1,4}'
        masked = re.sub(ipv4_pattern, 'x.x.x.x', condensed_hops[0], count=1)
        if masked == condensed_hops[0]:
            masked = re.sub(ipv6_pattern, 'x:x:x:x', condensed_hops[0], count=1)
        condensed_hops[0] = masked

    trace_mode_norm = (trace_mode or "icmp").strip().lower()
    if trace_mode_norm == "tcp":
        trace_mode_text = "TCP 模式"
    elif trace_mode_norm == "udp":
        trace_mode_text = "UDP 模式"
    else:
        trace_mode_text = "ICMP 模式"

    safe_server = html.escape(str(server_name))
    safe_target = html.escape(str(target))
    mode_label = "直接执行" if str(ip_type).strip().lower() == "direct" else html.escape(str(ip_type))

    result = "<b>【女仆 NextTrace 路由追踪结果】</b>\n\n"
    result += f"测试节点: {safe_server}\n"
    result += f"目标: {safe_target}\n"
    result += f"执行模式: {mode_label} ({trace_mode_text})\n\n"

    filtered_header = [html.escape(h) for h in header_lines if h]
    if filtered_header:
        header_block = "\n".join(filtered_header)
        if len(header_block) > 600:
            header_block = header_block[:600] + "\n... (头部已截断)"
        result += "<b>头部信息</b>:\n"
        result += "<pre>" + header_block + "</pre>\n\n"

    if not found_mode:
        result += "女仆没有找到路由信息，可能是 NextTrace 输出异常。\n"
        return result

    footer = f"<b>{html.escape(map_url_line)}</b>\n" if map_url_line else "女仆没有发现 MapTrace URL。\n"

    if condensed_hops:
        result += "<b>路由跳数</b>:\n"
        budget = max(200, 3950 - len(result) - len(footer) - len("<pre></pre>\n\n"))
        formatted_hops = []
        used = 0
        truncated = False
        for hop in condensed_hops:
            safe_hop = html.escape(hop)
            sep_len = 2 if formatted_hops else 0
            if used + sep_len + len(safe_hop) > budget:
                truncated = True
                break
            if formatted_hops:
                formatted_hops.append("")
            formatted_hops.append(safe_hop)
            used += sep_len + len(safe_hop)
        if truncated:
            formatted_hops.append("... (输出过长，后续跳数已截断)")
        result += "<pre>" + "\n".join(formatted_hops) + "</pre>\n\n"
    else:
        result += "女仆没有捕获到路由跳数信息。\n"

    result += footer
    return result


def ping_on_server(server_info: dict, target: str, ping_count: int = 4, display_name: str = None) -> str:
    ok, err = validate_target(target, display_name)
    if not ok:
        return err

    try:
        ping_count = max(1, min(50, int(ping_count)))
    except (TypeError, ValueError):
        ping_count = 4

    def ssh_connect_and_execute():
        # shlex.quote 二次防御，防止目标地址被当作 shell 语法解析
        cmd = f"ping -c {ping_count} {shlex.quote(target.strip())}"
        output, error, exit_status = _exec_ssh_command(server_info, cmd, cmd_timeout=20)
        if error.strip() and (exit_status != 0 or not output.strip()):
            return f"命令执行出错：\n{error}"
        return parse_ping_output(output)

    return retry_operation(ssh_connect_and_execute, retries=3, delay=2)


def nexttrace_on_server(server_info: dict, target: str, ip_type: str, trace_mode: str = "icmp", display_name: str = None) -> str:
    ok, err = validate_target(target, display_name)
    if not ok:
        return err

    def ssh_connect_and_execute():
        cmd_base = "nexttrace"
        ip_norm = (ip_type or "").strip().lower()
        mode_norm = (trace_mode or "icmp").strip().lower()

        if ip_norm == "ipv4":
            cmd_base += " -4"
        elif ip_norm == "ipv6":
            cmd_base += " -6"

        if mode_norm == "tcp":
            cmd_base += " --tcp"
        elif mode_norm == "udp":
            cmd_base += " --udp"

        # shlex.quote 二次防御，防止目标地址被当作 shell 语法解析
        cmd = f"{cmd_base} {shlex.quote(target.strip())}"
        output, error, exit_status = _exec_ssh_command(server_info, cmd, cmd_timeout=30)

        if "RetToken failed" in error or "RetToken failed" in output:
            return "路由追踪服务暂时不可用，请{who}稍后重试。".format(who=display_name or "这一位")
        if error.strip() and (exit_status != 0 or not output.strip()):
            return f"命令执行出错：\n{error}"
        return output

    return retry_operation(ssh_connect_and_execute, retries=3, delay=2)


def install_nexttrace_on_server(server_info: dict) -> str:
    def ssh_connect_and_execute():
        cmd = "curl -fsSL https://nxtrace.org/nt | bash"
        output, error, exit_status = _exec_ssh_command(server_info, cmd, cmd_timeout=60)
        combined_output = output + "\n" + error
        if "一切准备就绪" in combined_output:
            return "NextTrace 安装成功啦。"
        if exit_status != 0 or (error.strip() and not output.strip()):
            return f"命令执行出错：\n{error or output}"
        return f"安装输出：\n{output}\n\n女仆没检测到「一切准备就绪」，请主人手动确认安装状态。"

    return retry_operation(ssh_connect_and_execute, retries=3, delay=2)
