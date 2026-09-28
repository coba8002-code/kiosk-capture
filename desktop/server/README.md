# KFA 사내 AI 중계 서버

## 보안 구조

- Anthropic API 키는 서버의 Docker secret에만 둡니다.
- 진단 PC에는 공급자 키가 아니라 회전 가능한 사내 접근 토큰만 배포합니다.
- 촬영 이미지는 디스크와 로그에 저장하지 않고 메모리에서 Anthropic API로 전달합니다.
- 브라우저 Origin 요청은 차단합니다. KFA 데스크톱 클라이언트만 사용합니다.
- AI 결과는 위반 후보일 뿐이며 KFA 검토자가 최종 확정합니다.

> 사내 서버를 사용해도 Anthropic을 공급자로 쓰면 촬영 이미지는 사내 서버에서
> Anthropic API로 나갑니다. 개인정보 가림과 기관의 외부 전송 승인은 계속 필요합니다.

## 1. 키 발급

Anthropic Console의 API Keys 화면에서 서버 전용 키를 발급합니다.

https://console.anthropic.com/settings/keys

개인 계정의 일반 대화용 키와 분리하고, 가능하면 이 서비스 전용 워크스페이스와
사용 한도를 적용하십시오.

## 2. Linux 데이터서버 배포

필요 조건은 Docker Engine과 Docker Compose 플러그인입니다.

```bash
cd kiosk-field-audit/server
mkdir -p secrets
chmod 700 secrets
printf '%s' '발급받은_API_키' > secrets/anthropic_api_key.txt
openssl rand -hex 32 > secrets/gateway_token.txt
chmod 600 secrets/*.txt
cp .env.example .env
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8787/health
```

명령 기록에 API 키를 남기지 않으려면 실제 운영에서는 `read -s` 또는 서버 비밀관리
도구를 사용하십시오. `secrets/`와 `.env`는 Git에 포함되지 않습니다.

## 3. HTTPS 연결

`nginx-kfa.conf.example`을 회사 도메인과 인증서에 맞게 적용합니다. 컨테이너 포트는
127.0.0.1에만 열려 있으므로 외부 PC는 Nginx의 HTTPS 주소로만 접근합니다.
방화벽은 진단 PC가 속한 사내망 대역만 허용하십시오.

## 4. 진단 PC 한 번 설정

서버에서 `secrets/gateway_token.txt` 값을 안전한 사내 전달 경로로 관리자에게 전달합니다.
진단 PC에서 PowerShell을 열고 실행합니다.

```powershell
cd KFA-0.5.0\소스\server
.\setup-client.ps1 -ServerUrl 'https://kfa-ai.company.internal' -AccessToken (Read-Host '접근 토큰' -AsSecureString)
```

그 후 KFA.exe를 다시 실행하면 `AI 설정 → 사내 AI 서버`가 기본값입니다. 사용자는
Anthropic API 키를 입력하지 않습니다.

## 5. 운영 점검

```bash
docker compose ps
docker compose logs --tail=100 kfa-ai-gateway
curl https://kfa-ai.company.internal/health
```

로그에는 요청 ID, 항목 ID, 이미지 개수와 오류 종류만 기록합니다. 이미지·API 키·
프롬프트·모델 답변을 로그에 추가하지 마십시오. 접근 토큰과 Anthropic 키는 정기적으로
교체하고, 서버 백업에도 평문 secret 파일이 포함되지 않게 하십시오.

