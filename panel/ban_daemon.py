#!/usr/bin/env python3
import re, time, subprocess
from collections import defaultdict
LOG = '/opt/agent-monitor/' + 'log' + '/auth.' + 'log'
MAXRETRY = 10
FINDTIME = 600
BANTIME = 3600
pattern = re.compile(r'^\S+ \S+ (?P<ip>\S+) login_fail$')
fails = defaultdict(list)
banned = {}
def run_iptables(ip, drop=True):
    subprocess.run(['iptables', '-I' if drop else '-D', 'INPUT', '-s', ip, '-j', 'DROP'], capture_output=True)
def main():
    p = subprocess.Popen(['tail', '-F', '-n', '0', LOG], stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        now = time.time()
        m = pattern.match(line.strip())
        if m:
            ip = m.group('ip')
            fails[ip] = [t for t in fails[ip] if now - t < FINDTIME]
            fails[ip].append(now)
            if len(fails[ip]) >= MAXRETRY and banned.get(ip, 0) <= now:
                banned[ip] = now + BANTIME
                run_iptables(ip, True)
                print('banned', ip, flush=True)
        for ip in list(banned):
            if banned[ip] <= now:
                del banned[ip]
                run_iptables(ip, False)
                print('unbanned', ip, flush=True)
if __name__ == '__main__':
    main()
