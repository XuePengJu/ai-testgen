"""百炼（通义千问）客户端封装。仅在配置了 DASHSCOPE_API_KEY 时使用。"""
from config import settings


class BailianClient:
    def __init__(self, model: str = settings.BAILIAN_MODEL):
        self.model = model
        self.api_key = settings.DASHSCOPE_API_KEY
        import dashscope
        dashscope.api_key = self.api_key
        self.dashscope = dashscope

    def generate(self, prompt: str) -> str:
        """调用通义千问，返回文本。"""
        resp = self.dashscope.Generation.call(
            model=self.model,
            prompt=prompt,
            result_format="message",
        )
        if resp.status_code != 200:
            raise Exception(f"百炼调用失败: {resp.code} - {resp.message}")
        return resp.output.choices[0].message.content
