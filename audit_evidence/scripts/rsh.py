import sys, paramiko
host, user, src = sys.argv[1], sys.argv[2], sys.argv[3]
cmd = open(src, encoding="utf-8").read()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, 22, user, key_filename=r"C:\Users\Matt C\.ssh\id_ed25519", timeout=15)
stdin, stdout, stderr = c.exec_command(cmd, timeout=int(sys.argv[4]) if len(sys.argv) > 4 else 120)
out = stdout.read().decode("utf-8", "replace"); err = stderr.read().decode("utf-8", "replace")
sys.stdout.buffer.write(out.encode("ascii", "replace")); 
if err.strip():
    sys.stdout.buffer.write(b"\n--STDERR--\n" + err.encode("ascii", "replace"))
c.close()
