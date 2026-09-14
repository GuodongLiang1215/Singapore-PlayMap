"""Only app-authored messages reach the terminal/report; never provider bodies."""
from __future__ import annotations
from .error_diagnostics import safe_diagnostic, KINDS

MESSAGES = {
    'KEY_MISSING': '尚未配置 Gemini Key，请先运行 scripts/setup_gemini.py。',
    'KEY_FORMAT': '本地输入检查未通过；请用 AI Studio 的复制按钮复制完整 Key。允许点号和长密钥；这不是 Google 的有效性判断。',
    'KEY_LENGTH': '输入未通过本地长度保护（20—16384字符）；可能未粘贴完整或复制了其他内容。这不是 Google 官方长度规格。',
    'KEY_CONTROL': '检测到终端控制字符，可能没有正确粘贴。请运行 scripts/setup_gemini.py --gui，在密码框中重新粘贴。',
    'KEY_MULTILINE': '检测到多行内容。请只复制一个完整 Key，不要复制代码块；不会自动拼接或删除内部换行。',
    'KEY_WHITESPACE': 'Key 内部含空格或其他空白。请用复制按钮重取完整 Key；程序不会擅自删除内部字符。',
    'KEY_INVISIBLE': '检测到不可见字符。请重新复制完整 Key；程序不会改写密钥中的字符。',
    'KEY_WRAPPER': '复制内容包含赋值语句、引号、URL 或代码包装。密码框中只需 Key 本身，不要粘贴整行配置。',
    'KEY_MASKED': '输入包含星号、圆点或省略号，可能复制了遮掩后的预览。请使用密钥页面的复制按钮获取完整值。',
    'KEY_PLACEHOLDER': '粘贴的是示例占位符，不是真正的 Key。请从自己的 AI Studio 项目复制完整值。',
    'GUI_UNAVAILABLE': '本机无法打开 Tk 密码窗口。可继续使用原终端输入；或在本机 .env 更新 GEMINI_API_KEY、LLM_PROVIDER、LLM_MODEL，不要分享文件。',
    'MODEL_FORMAT': '模型标识格式无效；应使用 gemini-3.5-flash-lite 这样的 API 标识。',
    'PROVIDER_MISMATCH': 'LLM_PROVIDER 不是 gemini。本测试不会自动改用其他服务商。',
    'ENV_FILE': '无法安全读取或写入 .env；请检查 UTF-8 编码、文件权限及是否为链接。',
    'ENV_CONFLICT': '.env 在保存期间被其他进程修改；没有替换，请关闭其他配置编辑后重试。',
    'HIDDEN_INPUT': '无法隐藏输入。请在 VS Code 的交互式 PowerShell 终端中运行。',
    'KEY_INVALID': '服务商拒绝此 Key，请在 AI Studio 检查密钥状态、项目及 API 限制。',
    'ACCESS_DENIED': '服务商拒绝访问；请检查 Key 的 API/来源限制、项目权限及所在地区的服务可用性。',
    'MODEL_NOT_AVAILABLE': '这个 Key 下未取得指定模型。不会自动换模型；可运行 --list-models 查看返回清单。',
    'RATE_LIMIT': '服务商返回 429，可能是速率或配额限制。本脚本不自动重试；请查看当前项目 Rate limits。',
    'BAD_REQUEST': '服务商不接受此次请求参数；不会自动换模型、关闭格式检查或重试。',
    'REDIRECT_BLOCKED': 'API 返回重定向；为避免把密钥发到其他地址，本脚本拒绝跟随。',
    'PROVIDER_UNAVAILABLE': '服务商暂不可用或返回异常状态；没有自动重试。',
    'NETWORK_ERROR': '无法连接官方 API。请检查网络、代理和证书；不要关闭 TLS 证书验证。',
    'TIMEOUT': '官方 API 请求超时；服务端可能已经处理了请求，不自动重发以免额外消耗额度。',
    'BAD_RESPONSE': '服务商响应格式无效或体积超出本测试限制；未保存原始响应。',
    'MODEL_METADATA': '模型元数据与请求不一致，或未声明支持 generateContent；未进行生成调用。',
    'GENERATION_BLOCKED': '响应被服务商阻止或没有生成候选；没有伪造模型结果。',
    'INCOMPLETE_OUTPUT': '模型输出未正常结束，或输出被截断；没有将不完整内容当作成功结果。',
    'SCHEMA_MISMATCH': '模型返回内容不符合本地结构检查；没有自动补字段或修复成预期答案。',
    'SEMANTIC_MISMATCH': '返回结构有效，但与固定测试需求不一致；这不是通过。',
    'UNEXPECTED_TOOL': '本连接测试没有启用工具，但响应含非文本操作；已拒绝处理。',
    'UNEXPECTED_ERROR': '出现未预期的本地错误；为避免泄露凭据，不输出原始异常文本。',
    'CANCELLED': '用户取消了检查；已记录取消状态。',
    'NO_MODEL_LIST': '模型清单格式异常或分页未完整结束；不能宣称完整清单已获得。',
}

class LLMConnectionError(Exception):
    def __init__(self, code: str, http_status: int | None = None, *, diagnostic: dict | None = None):
        self.code = code if code in MESSAGES else 'UNEXPECTED_ERROR'
        self.message = MESSAGES[self.code]
        self.http_status = http_status
        self.diagnostic = safe_diagnostic(diagnostic)
        if self.code == 'BAD_REQUEST' and self.diagnostic:
            self.message += ' ' + KINDS[self.diagnostic['kind']]
        super().__init__(self.message)

    def public(self) -> dict:
        result = {'code': self.code, 'message': self.message, 'http_status': self.http_status}
        if self.diagnostic is not None: result['provider_diagnostic'] = self.diagnostic
        return result
