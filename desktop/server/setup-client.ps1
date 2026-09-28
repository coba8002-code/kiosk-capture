param(
    [Parameter(Mandatory=$true)][string]$ServerUrl,
    [Parameter(Mandatory=$true)][SecureString]$AccessToken
)

$ErrorActionPreference = 'Stop'
$uri = [Uri]$ServerUrl
if ($uri.Scheme -ne 'https') {
    throw '운영 서버 주소는 https:// 로 시작해야 합니다.'
}
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($AccessToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ($token.Length -lt 32) { throw '접근 토큰은 32자 이상이어야 합니다.' }
    [Environment]::SetEnvironmentVariable('KFA_GATEWAY_URL', $ServerUrl.TrimEnd('/'), 'User')
    [Environment]::SetEnvironmentVariable('KFA_GATEWAY_TOKEN', $token, 'User')
    [Environment]::SetEnvironmentVariable('KFA_L2_PROVIDER', 'gateway', 'User')
} finally {
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    $token = $null
}
Write-Host 'KFA 사내 AI 서버 설정을 저장했습니다.'
Write-Host '열려 있는 KFA.exe를 닫고 다시 실행하십시오.'

