# Example: test against the appliance over an SSH tunnel (e.g. when away from home).
# Forward the MCP (13282) and UserApp (80) ports, then run the curl tests locally.

ssh -L 13282:localhost:13282 -L 9080:localhost:80 user@appliance
./tests/curl-tests.sh http://localhost:13282 http://localhost:9080

curl -s http://localhost:9080/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"Password2026"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])"
