# Handoff — 웹 대시보드 Git 페이지 · Chat 페이지

- **브랜치**: `feat/add-git-repo-menu` (base: `dev` @ `78ed44b`, 총 16커밋)
- **작성일**: 2026-08-06
- **상태**: 구현·자동화 테스트·브라우저 E2E(실 claude CLI 포함) 완료. dev 머지/푸시는 미실행 (operator 결정 사항)

---

## 1. 추가된 기능

### Git 페이지 (`#/git`)
연결된 host repo의 git 상태를 대시보드에서 직접 관리한다.

| 기능 | 설명 |
|---|---|
| Task branches | `symphony/<ID>` 브랜치 ↔ kanban 티켓 매핑, merged/`↑ahead ↓behind`/running 배지 |
| History | repo 전체 또는 브랜치별 커밋 로그 (sha, ref 칩, 작성자·시간) |
| Compare | merge 미리보기 — target 대비 커밋 목록 + 파일별 diffstat |
| Changes 패널 (우측 고정) | Compare 로드/커밋 클릭 시 파일별 접이식 unified diff (+초록/−빨강) |
| **수동 merge** (유일한 mutation) | task 브랜치를 target으로 merge. 자동 Verify 게이트와 동일 엔진(`auto_merge_on_done_best_effort`) 재사용 — exclude_paths·`--no-ff`·충돌 사전검사·dirty 가드 동일. Merge Gate 실패로 Blocked된 티켓 복구 용도 포함 |

### Chat 페이지 (`#/chat`)
WORKFLOW.md에 연결된 agent(claude/codex/...)와 host repo에 대해 실시간 대화.

| 기능 | 설명 |
|---|---|
| Q&A 모드 | 읽기 전용 (claude `--permission-mode plan`, codex read-only sandbox). repo 질의응답 |
| Edit 모드 | 같은 워킹트리에서 공동 작업 — 파일 수정, **kanban 이슈 등록** (턴 종료 시 `request_refresh()`로 즉시 픽업) |
| WebSocket 스트리밍 | 중간 메시지·툴 활동 실시간 표시, 재연결 시 seq 중복 제거 + 최근 100개 복원 |
| 모드 전환 | claude는 `--resume`으로 대화 유지 + 다음 메시지에 모드 변경 공지 프리펜드, codex는 세션 재시작(경고 표시) |
| 글자 크기 | 기본 15px, A−/A+ 버튼 12–20px, localStorage 저장 |
| 기록 | 메모리 500개 + `<repo>/.symphony/chat/<세션>.jsonl` 영구 기록 (서버 재시작 시 라이브 세션은 종료, 파일은 잔존) |

### 안전 장치
- merge: `symphony/*` 접두사 화이트리스트 → 러닝 워커 409 → 단일 실행 락 → auto_merge 자체 가드(RC 41~53)
- chat: 워커 슬롯 미점유(티켓 굶김 없음), 턴 단일 실행, loopback Host 가드 + **WS cross-origin 403**, mutation은 REST(JSON 강제)만
- 티켓 상태 전이는 절대 자동으로 하지 않음 (merge 후 Blocked 해제는 기존 Recover 플로우)

---

## 2. 코드 맵

| 파일 | 역할 |
|---|---|
| `src/symphony/utils/git_inspect.py` | read-only git 조회 (log/branches/task-branches/compare/diff). 실패 시 빈 결과로 degrade |
| `src/symphony/chat.py` | ChatManager — 세션 수명주기, 모드 변형(`cfg_for_mode`), 이벤트 fan-out, JSONL writer |
| `src/symphony/webapi.py` | `_register_git_routes` (`/api/v1/git/*`), `_register_chat_routes` (`/api/v1/chat/*` + WS) |
| `src/symphony/errors.py` | `ChatBusyError` / `ChatSessionExistsError` / `ChatNoSessionError` |
| `src/symphony/workflow/constants.py` | `SYMPHONY_BRANCH_PREFIX` (하드코딩 추출) |
| `src/symphony/web/static/{index.html,app.js,style.css}` | 사이드바 2메뉴 + Git/Chat 페이지 + WS 클라이언트 |
| `tests/test_git_inspect.py` (11) | git 조회 단위 (temp repo) |
| `tests/test_webapi.py` (+git 8) | git REST 계약 + 실 merge E2E |
| `tests/test_chat.py` (13) | ChatManager 단위 (fake backend) |
| `tests/test_webapi_chat.py` (5) | chat REST/WS 계약 |
| `tests/test_web_static_contract.py` (5) | SPA 문자열 계약 |

무수정: `backends/*`, `orchestrator/*`(상수 교체 3줄 제외), `server.py`, `cli/main.py`, `workflow/config.py`

---

## 3. 실행 방법

### A. 실서버 (이 repo)
```bash
symphony run ./WORKFLOW.md          # 또는 symphony service start
# 브라우저: http://127.0.0.1:9999/#/git , #/chat
```
- 사이드바 표시등이 **초록**이어야 정상.
- ⚠️ 실서버는 kanban의 Todo 티켓을 실제로 dispatch한다. QA만 하려면 아래 스텁 서버 권장.

### B. QA용 스텁 데모 서버 (dispatch 없음, board+git+chat 모두 동작)

아래 전문을 `/tmp/symphony_qa_demo.py`로 저장 후 실행한다.

```python
# /tmp/symphony_qa_demo.py — QA demo: full web UI over a scratch repo.
# Chat은 실제 agent CLI를 스폰한다 (토큰 소모).
import subprocess, sys, tempfile
from pathlib import Path
from typing import cast
from aiohttp import web

REPO = Path("/Users/chaeseong-gug/Documents/PARA/Project/Git/symphony-multi-agent")
sys.path.insert(0, str(REPO / "tests"))
from test_webapi import _StubOrchestrator, WORKFLOW_TEXT, TICKET  # 풀 스텁: board까지 동작

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


tmp = Path(tempfile.mkdtemp(prefix="symphony-qa-"))
(tmp / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
stages = tmp / "prompts" / "stages"; stages.mkdir(parents=True)
(stages / "todo.md").write_text("todo"); (stages / "doing.md").write_text("doing")
(tmp / "kanban").mkdir(); (tmp / "kanban" / "SEED-1.md").write_text(TICKET, encoding="utf-8")
(tmp / "README.md").write_text("# qa-demo\n"); (tmp / "calc.py").write_text("def add(a, b):\n    return a + b\n")
_git(tmp, "init", "-q", "-b", "main"); _git(tmp, "add", "-A"); _git(tmp, "commit", "-q", "-m", "init board")
_git(tmp, "checkout", "-q", "-b", "symphony/SEED-1"); (tmp / "feature.py").write_text("print('hi')\n")
_git(tmp, "add", "feature.py"); _git(tmp, "commit", "-q", "-m", "SEED-1: feature"); _git(tmp, "checkout", "-q", "main")

state = WorkflowState(tmp / "WORKFLOW.md"); cfg, err = state.reload(); assert err is None, err
print(f"demo board: {tmp}", flush=True)
web.run_app(build_app(cast(Orchestrator, _StubOrchestrator(state))), host="127.0.0.1", port=9920)
```
```bash
.venv/bin/python /tmp/symphony_qa_demo.py   # http://127.0.0.1:9920
```
- 이 스텁은 board 표면까지 구현하므로 표시등이 초록이다. **chat은 실 agent CLI를 스폰**하므로 토큰이 소모된다.
- 참고: 채팅 전용 최소 스텁(test_webapi_chat의 것)을 쓰면 board가 500이라 "Orchestrator unreachable"가 뜨는데, 이는 **스텁 한계이지 버그가 아니다** — 채팅 라우트는 오케스트레이터를 사용하지 않아 정상 동작한다.

---

## 4. 자동화 테스트

```bash
.venv/bin/pytest -q                 # 전체
.venv/bin/pytest -q tests/test_git_inspect.py tests/test_webapi.py \
    tests/test_chat.py tests/test_webapi_chat.py tests/test_web_static_contract.py
.venv/bin/ruff check .
.venv/bin/pyright                   # 기준선 27 errors (환경 문제, 브랜치 base와 동일)
.venv/bin/symphony doctor ./WORKFLOW.md
```

**기대 결과**: `1455 passed, 6 skipped, 1 failed` — 실패는
`test_continuous_improvement.py::test_run_continuous_improvement_real_git_target_worktree_e2e`
하나로, **base 커밋(78ed44b)에서도 동일하게 실패하는 이 머신의 기존 실패**다 (이번 변경과 무관, CI에서는 통과 이력 있음).

---

## 5. 수동 QA 체크리스트 — Git 페이지

스텁 데모 서버(§3-B) 기준. `DEMO=$(demo board 출력 경로)`.

> G1·G2·G4·G5와 §7 API 스모크, C11 보안 검사는 이 문서를 쓰면서 §3-B 스크립트로 1회 실행해
> 기대 결과를 확인했다 (merge 커밋 생성 + `Manual Merge` 노트 기록까지). 나머지 항목과
> Chat 체크리스트(실 CLI 토큰 소모)는 인수자가 실행한다.

- [ ] **G1 조회**: `#/git` 진입 → Task branches에 `symphony/SEED-1`(티켓 칩 `SEED-1 · Todo`, `↑1 ↓0`), History에 커밋 2개(ref 칩 포함)
- [ ] **G2 Compare**: SEED-1 행의 [Compare] → Compare 카드에 `↑1 ↓0`, 커밋 1개, diffstat `feature.py +1` / 우측 Changes 패널에 `feature.py` unified diff(`+print('hi')` 초록)
- [ ] **G3 커밋 diff**: History의 커밋 행 클릭 → Changes 패널에 해당 커밋 patch (머지 커밋은 헤더만 — git show의 정상 동작)
- [ ] **G4 수동 merge**: SEED-1 행 [Merge] → 모달(target=main) → Merge → 성공 토스트, 행이 `merged` 배지 + 버튼 비활성, History에 `merge: SEED-1 ...` 커밋
  ```bash
  git -C "$DEMO" log --oneline main | head -2     # merge 커밋 확인
  grep -A2 "Manual Merge" "$DEMO/kanban/SEED-1.md" # 티켓 노트 확인
  ```
- [ ] **G5 merge 가드**: merged 상태에서 Merge 버튼 비활성 확인. curl로 직접 검증:
  ```bash
  curl -s -X POST http://127.0.0.1:9920/api/v1/git/merge \
    -H 'Content-Type: application/json' -d '{"branch":"main"}'          # 400 (task 브랜치만)
  curl -s -X POST http://127.0.0.1:9920/api/v1/git/merge \
    -H 'Content-Type: application/json' -d '{"branch":"symphony/../x"}' # 400
  ```
- [ ] **G6 degrade**: git repo가 아닌 보드에서 200 + `note: "not_a_git_repo"` (자동 테스트 커버, 수동 생략 가능)
- [ ] **G7 실서버 통합** (선택): 실서버에서 Merge Gate 실패로 Blocked된 티켓의 브랜치를 수동 merge → 티켓 노트 확인 → 보드에서 Recover

## 6. 수동 QA 체크리스트 — Chat 페이지

⚠️ 실 agent CLI 스폰 — 토큰 소모.

- [ ] **C1 세션 시작**: `#/chat` → 모드 `Q&A` + [Start session] → `claude` 배지, Q&A/Edit 토글, 입력창 활성
- [ ] **C2 질의응답 스트리밍**: "이 repo에 어떤 파일이 있어?" 전송 → 사용자 말풍선(우측), "Agent is working…" 인디케이터, 툴 활동 줄(mono), 마크다운 답변 말풍선. 진행 중 입력창 비활성
- [ ] **C3 qa 읽기 전용**: qa 모드에서 "파일 만들어줘" → 에이전트가 거부하고 edit 모드 안내
- [ ] **C4 모드 전환**: [Edit] 클릭 → (claude) 대화 문맥 유지 확인 — 이전 대화 내용을 참조하는 후속 질문에 정상 답변
- [ ] **C5 공동 작업**: edit 모드에서 "calc.py에 subtract 함수 추가해줘" → `git -C "$DEMO" diff calc.py`로 실제 변경 확인
- [ ] **C6 이슈 등록**: "kanban 보드에 multiply 함수 추가 이슈 등록해줘 (priority 2, label demo)" → `ls "$DEMO/kanban/"`에 새 `.md`, front matter(`state: Todo`, priority, labels) 확인. Board 페이지에서 새 티켓 표시
- [ ] **C7 busy 가드**: 턴 진행 중 입력창·Send 비활성. curl 이중 전송 시 409 `chat_busy`
- [ ] **C8 WS 재연결**: 페이지 새로고침 → 최근 대화 복원(중복 없음). 서버 껐다 켜면 재연결 백오프 후 hello 수신
- [ ] **C9 폰트**: A+ 수회 → 말풍선·툴 줄·입력창 동반 확대 → 새로고침 후 유지 (localStorage `symphony.chatFontSize`)
- [ ] **C10 기록/정리**: `ls "$DEMO/.symphony/chat/"`에 세션 JSONL. [Stop] → 스냅샷 `{active:false}`. 서버 종료 시 agent 프로세스 잔존 없음(`pgrep -f "claude -p"`)
- [ ] **C11 보안**:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: http://evil.example' \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Key: dGVzdA==' -H 'Sec-WebSocket-Version: 13' \
    http://127.0.0.1:9920/api/v1/chat/ws                        # 403
  curl -s -o /dev/null -w '%{http_code}\n' -X POST -d 'x' \
    http://127.0.0.1:9920/api/v1/chat/message                   # 415 (JSON 강제)
  ```

## 7. API 레퍼런스 스모크

```bash
B=http://127.0.0.1:9920/api/v1
curl -s $B/git/branches | jq .
curl -s "$B/git/log?limit=5" | jq '.commits[].subject'
curl -s $B/git/task-branches | jq '.branches[0]'
curl -s "$B/git/compare?branch=symphony/SEED-1" | jq '.stat.total'
curl -s "$B/git/diff?branch=symphony/SEED-1" | jq -r '.patch' | head
curl -s $B/chat/session | jq .
# 세션 생성 → 메시지 → 종료
curl -s -X POST $B/chat/session -H 'Content-Type: application/json' -d '{"mode":"qa"}' | jq .mode
curl -s -X POST $B/chat/message -H 'Content-Type: application/json' -d '{"text":"hi"}' -o /dev/null -w '%{http_code}\n'  # 202
curl -s -X DELETE $B/chat/session | jq .
```

## 8. 알려진 제약 · 의도된 동작

- **스텁 데모의 "Orchestrator unreachable"**: 채팅 전용 최소 스텁은 `/api/v1/board`를 못 채워 표시등이 빨강 — 버그 아님. §3-B의 풀 스텁 또는 실서버에서는 초록
- 채팅에는 토큰 예산이 없다 — 컨텍스트 관리(auto-compact)는 agent CLI에 위임. 장시간 세션 비용 상한이 필요하면 후속 과제
- 서버 재시작 시 채팅 라이브 세션 소멸(설계). JSONL 기록은 잔존, 화면 복원 없음
- gemini/agy/kiro/opencode/pi는 read-only 강제 불가 → `read-only not enforced` 배지 + 프리앰블 소프트 제어
- Linear/Jira 보드: git task-branches의 `ticket`이 null로 degrade, 채팅 이슈 등록 프리앰블 미주입 (file 보드 전용)
- `_apply_dispatch_env`의 전역 env와 채팅 스폰이 겹치면 정보성 `SYMPHONY_TOKEN_*`이 새어들 수 있음 (무해, chat.py docstring 문서화)
- merge는 자동 게이트와 동일하게 호스트 repo에서 `git checkout $TARGET`을 수행

## 9. 후속 아이디어 (미구현)

토큰 단위 타이핑(`--include-partial-messages` 델타 파싱, backends 무수정 가능) · 채팅 토큰/턴 예산 · 서버 재시작 후 세션 재접속(`~/.claude` 세션 파일 활용) · 멀티 세션 · 브랜치 정리(삭제)/push/PR

## 10. 반출 절차

```bash
git push                            # 훅이 차단 시 operator가 직접 실행
# dev 머지는 PR 또는 로컬 --no-ff 머지로 operator가 결정
```
