Community
============
Server Status now has a community forum open to everyone. https://www.pilabs.io/forum/

ServerStatus
============

ServerStatus is based off [BlueVM's](http://uptime.bluevm.com/) Uptime Checker script, [original download and information](http://www.lowendtalk.com/discussion/comment/169690#Comment_169690).

It uses Bootstrap for theming and progress bars.

You can currently see Load, RAM (free), HDD (free) statistics, and if it is online or not.

Screenshot
============
![Screenshot](http://www.mojeda.com/wp/wp-content/2013/04/serverupbigthemes.png)
![Mobile Screenshot](http://www.mojeda.com/wp/wp-content/2013/04/serverupthemes.png)

Installation
============

1. Create a database with a user.
2. Import the servers.sql file in in the /sql/ folder, to populate the database.
3. Configure /includes/config.php with the database and user information.
4. Copy uptime.php to any server you want to monitor. This needs to be publicly accessible.
5. Insert an entry into the database.
  * name - The name of your server.
  * url - The URL path to the uptime.php file (minus uptime.php and http://) e.g. dns.domain.tld/path/
  * location - Where is your server physically located?
  * host - The name of the host of which your server is hosted by.
  * type - What type of server is this? DNS, SQL, Apache/nginx, etc.

Requirements
============

**Remote Servers**:
* PHP5, currently php_exec needs to be enabled in order to get the uptime.
* Web Server (lighttpd, apache2, nginx, etc.)
* You do **NOT** need a database running on the remote servers.

**Master Server**:
* PHP5 + PHP5_CURL
* Web Server (lighttpd, apache2, nginx, etc.)
* mySQL server unless you choose to use a remote mySQL server.

GitHub Actions 서버 모니터링
============

기존 PHP/MySQL 페이지와 독립적으로 실행되는 외부 모니터입니다. PHP 파일이나 DB 설정을 변경하지 않으며, 결과는 **Actions 실행 요약과 GitHub Issues**에서 확인합니다. 기존 웹 페이지에 검사 결과를 저장하지 않습니다.

### 시작하기

1. 이 변경을 기본 브랜치(`master`)에 병합합니다. 저장소의 **Actions**와 **Issues**가 활성화되어 있어야 합니다.
2. `monitoring/targets.json`에서 감시 대상을 관리합니다. 현재 대상은 `106.249.253.66` ICMP IPv4와 `mon-01.wering.net:22` TCP IPv4입니다.
3. **Actions → Server monitor → Run workflow**로 수동 실행합니다. 이후 UTC 기준 매시 2, 7, 12, …, 57분에 실행됩니다.
4. 실패한 검사는 고유 ID별로 Issue를 하나 생성하고, 후속 검사 성공 시 복구 댓글을 남긴 뒤 닫습니다. 계속 실패하면 같은 Issue를 유지하며, 복구 후 재발하면 새 Issue를 만듭니다.

`GITHUB_TOKEN`을 자동으로 사용하므로 별도 PAT는 필요하지 않습니다. 워크플로 권한은 `contents: read`, `issues: write`입니다. 조직 정책에서 Issue 쓰기를 막는 경우 정책 조정이 필요합니다. Issue 구독/알림 설정에 따라 GitHub 알림을 받을 수 있습니다.

### 대상 추가 예시

`targets` 배열에 객체를 추가합니다. 전체 예시는 `monitoring/targets.example.json`에 있으며, 이 예시 파일은 자동 실행 대상이 아닙니다.

```json
{
  "targets": [
    {"id": "origin-ping-v4", "type": "icmp", "host": "106.249.253.66", "family": 4},
    {"id": "origin-ping-v6", "type": "icmp", "host": "2001:db8::10", "family": 6},
    {"id": "origin-ssh-v4", "type": "tcp", "host": "mon-01.wering.net", "port": 22, "family": 4},
    {"id": "origin-http-v4", "type": "http", "url": "http://server.example.com/health", "family": 4, "expected_status": [200]},
    {"id": "origin-https-v6", "type": "http", "url": "https://server.example.com/health", "family": 6, "expected_status": [200, 204], "timeout": 10, "attempts": 2}
  ]
}
```

예시 도메인과 `2001:db8::10`은 실제 주소로 교체해야 합니다.

| 필드 | 설명 |
| --- | --- |
| `id` | 중복 없는 영문 소문자·숫자·`-`·`_` 식별자(최대 80자). Issue 연결 키이므로 대상·프로토콜·주소군마다 별도 ID 사용 |
| `type` | `icmp`, `tcp`, `http` (`http`는 HTTP/HTTPS 모두 지원) |
| `family` | `4` 또는 `6`. DNS 조회와 연결에 해당 주소군만 사용하며 다른 주소군으로 대체하지 않음 |
| `host` / `port` | ICMP/TCP 호스트, TCP 필수 포트(1–65535) |
| `url` / `expected_status` | HTTP(S) 주소와 허용 상태 코드 배열. 기본 `[200]` |
| `timeout` | 시도당 전체 제한 시간(초). 기본 10, 범위 1–30. DNS 조회도 제한 시간에 포함 |
| `attempts` | 실패 시 총 시도 횟수. 기본 2, 범위 1–3. 한 번이라도 성공하면 정상 |
| `enabled` | 기본 `true`. `false`이면 검사 및 Issue 변경 생략 |

ICMP는 시도마다 echo 2개를 보내며 하나라도 응답하면 성공합니다. TCP는 연결 수립만 확인하므로 SSH 로그인이나 서비스 응답 내용은 검증하지 않습니다. HTTP는 GET 요청의 상태 코드를 확인하며 리다이렉트를 따라가지 않습니다. HTTPS 인증서를 검증합니다. 프록시 환경변수와 사용자 curl 설정을 무시하여 지정 주소군으로 직접 검사합니다. 비밀 토큰을 URL이나 공개 설정에 넣지 마세요.

### IPv6와 실행 환경

기본 실행 환경은 `ubuntu-latest`입니다. IPv6 검사 전 Linux의 IPv6 기본 경로를 확인하며, 없으면 IPv6 결과를 **UNKNOWN**으로 표시하고 해당 Issue를 생성하거나 닫지 않습니다. 이 경우 실행 자체는 실패로 표시하여 검사 공백을 드러냅니다. IPv4 검사는 계속합니다. 기본 경로 존재는 실제 인터넷 연결을 보장하지 않으므로, 경로는 있지만 연결이 안 되는 상황은 해당 관측 지점의 실패로 기록됩니다.

IPv6 또는 사설망 대상은 연결 가능한 Linux self-hosted runner를 사용하세요. 저장소 **Settings → Secrets and variables → Actions → Variables**에서 `MONITOR_RUNNER`를 JSON 값 `["self-hosted", "linux", "ipv6"]`로 설정하고 runner에 같은 라벨을 부여합니다. Python 3.9+, `ping`(iputils), `curl`, `ip`(iproute2)가 필요합니다. IPv6는 기본 경로가 있는 runner를 전제로 합니다. ICMP 송신 권한과 방화벽도 확인하세요. PR 테스트는 항상 GitHub-hosted runner에서 실행됩니다.

### 운영 및 검증

- 전체 설정을 검증한 후 검사를 시작합니다. 설정/도구 오류는 장애 Issue로 변환하지 않습니다. API 오류는 실행 실패로 표시하며 다음 실행에서 다시 조정합니다.
- 검사는 최대 10개씩 병렬 실행하며 활성 검사는 최대 50개입니다. 전체 작업은 10분으로 제한하고, schedule/수동 실행 간 동시 실행을 막아 Issue 중복 생성을 줄입니다.
- 결과가 DOWN 또는 UNKNOWN이면 실행 상태도 실패입니다. Summary에서 원인을 확인하세요. GitHub 자체나 runner 장애로 실행이 중단되면 이 모니터가 알릴 수 없으므로 중요한 서비스는 별도 관측 수단을 함께 두세요.
- Issue 본문 첫 줄의 `server-monitor:v1:<id>` 표식을 보존하세요. 봇이 만든 일치하는 열린 Issue만 자동 처리합니다. 대상 삭제·비활성화·ID 변경 시 기존 Issue는 자동 종료하지 않으므로 직접 정리하세요. 열린 장애 Issue를 수동 종료하면 다음 실패 때 새 Issue가 생성됩니다.
- GitHub 예약 실행은 [최소 5분 간격](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)을 지원하지만 [부하에 따라 지연 또는 누락](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)될 수 있습니다. 예약 실행은 기본 브랜치에서만 동작합니다. 공개 저장소는 [60일간 활동이 없으면 예약 실행이 비활성화](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)될 수 있습니다. 사용량과 결제 한도도 확인하세요.

Linux에서 아래 명령으로 검증할 수 있습니다. `--dry-run`은 실제 네트워크 검사를 수행하지만 Issue는 변경하지 않습니다.

```sh
python3 monitoring/monitor.py --validate
python3 monitoring/monitor.py --config monitoring/targets.example.json --validate
python3 -m unittest discover -s monitoring -v
python3 monitoring/monitor.py --dry-run
```

`Monitor tests` 워크플로는 PR 및 관련 파일 push에서 설정 검증, 장애 생성/중복 방지/복구/IPv6 검사 불가 처리와 로컬 TCP/HTTP 검사를 실행합니다. 외부 서버나 실제 Issues를 테스트에 사용하지 않습니다.
