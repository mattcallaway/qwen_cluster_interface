import pytest
from app.services.hardware_probe import (
    parse_free_output,
    parse_lscpu_output,
    parse_uname_output,
    parse_ip_output,
    recommend_role_and_model,
    test_tcp_reachability as check_tcp_reachability
)

def test_reachability_offline_host():
    is_up, msg = check_tcp_reachability("192.0.2.1", port=22, timeout=0.2)
    assert is_up is False
    assert "Connection" in msg or "timed out" in msg or "refused" in msg


def test_parse_free_output():
    raw_free = """
                  total        used        free      shared  buff/cache   available
    Mem:           3892        1200         800         120        1892        2400
    Swap:          2047         100        1947
    """
    res = parse_free_output(raw_free)
    assert res["ram_total_mb"] == 3892
    assert res["ram_avail_mb"] == 2400

def test_parse_lscpu_output():
    raw_lscpu = """
    Architecture:                    x86_64
    CPU op-mode(s):                  32-bit, 64-bit
    Address sizes:                   36 bits physical, 48 bits virtual
    Byte Order:                      Little Endian
    CPU(s):                          4
    On-line CPU(s) list:             0-3
    Vendor ID:                       GenuineIntel
    Model name:                      Intel(R) Core(TM) i5-3320M CPU @ 2.60GHz
    """
    res = parse_lscpu_output(raw_lscpu)
    assert res["arch"] == "x86_64"
    assert res["cores"] == 4
    assert "Intel(R) Core(TM) i5-3320M" in res["model"]

def test_parse_uname_output():
    raw_uname = "Linux thinkpad 5.10.0-8-amd64 #1 SMP Debian 5.10.46-4 x86_64 GNU/Linux\n"
    res = parse_uname_output(raw_uname)
    assert "Linux thinkpad 5.10.0-8-amd64" in res["os_kernel"]

def test_parse_ip_output():
    raw_ip = """
    1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
        inet 127.0.0.1/8 scope host lo
    2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
        inet 192.168.1.105/24 brd 192.168.1.255 scope global eth0
    """
    res = parse_ip_output(raw_ip)
    assert "192.168.1.105" in res
    assert "127.0.0.1" not in res

def test_role_recommendation_4gb_laptop():
    # 4GB RAM laptop
    rec = recommend_role_and_model(3892, "x86_64")
    assert rec["recommended_role"] == "distributed_worker"
    assert "typical ~4 GB Linux laptop" in rec["explanation"]

def test_role_recommendation_8gb():
    rec = recommend_role_and_model(8000, "x86_64")
    assert rec["recommended_role"] == "standalone_server"

def test_role_recommendation_vps():
    rec = recommend_role_and_model(2048, "x86_64", is_vps=True)
    assert rec["recommended_role"] == "vps_worker"

def test_role_recommendation_unknown():
    rec = recommend_role_and_model(None, None)
    assert rec["recommended_role"] == "unassigned"
