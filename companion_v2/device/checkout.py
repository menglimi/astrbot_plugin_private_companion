"""Checkout decisions are held for the original private requester."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import re

from PIL import Image

from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS

from .adb import select_elements
from .session import PhoneAction, parse_action


PAY_LABEL = re.compile(
    r"提交订单|确认下单|立即下单|确认订单并|下单并支付|确认支付|立即支付|极速支付|去支付|确认付款|立即付款|去付款"
    r"|先用后付|免密支付|(?:付款|支付)[¥￥]|\b(?:pay now|place order)\b", re.I)
CHECKOUT_PAGE = re.compile(r"确认订单|订单确认|提交订单|支付方式|实付金额|应付金额|合计[¥￥]", re.I)
AMOUNT = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,2})?")
APPROVAL_COMMAND = "确认手机订单"
QUICK_APPROVAL = "确认支付"
CANCEL_COMMAND = "取消手机订单"
MEITUAN_PACKAGE = "com.sankuai.meituan"
MEITUAN_EXPRESS_PAYMENT = "极速支付"
MEITUAN_CUTLERY_SUBMIT = "确认并提交订单"
MEITUAN_PAID_LABELS = {"支付成功", "付款成功", "订单已支付", "等待商家接单", "商家已接单"}


@dataclass(frozen=True)
class CheckoutDecision:
    merchant: str
    item: str
    options: str
    amount: str
    address_hint: str
    button: PhoneAction

    @classmethod
    def parse(cls, value):
        fields = {"merchant", "item", "options", "amount", "address_hint", "button"}
        if type(value) is not dict or value.keys() != fields:
            raise ValueError("checkout needs visible order details and its button")
        details = {}
        for name in fields - {"button"}:
            field = value[name]
            if type(field) is not str or not field.strip() or len(field) > 120:
                raise ValueError("checkout details must be short visible text")
            details[name] = " ".join(field.split())
        if not AMOUNT.fullmatch(details["amount"]):
            raise ValueError("checkout requires a visible RMB total")
        button = parse_action(value["button"])
        if button.kind not in ("tap", "tap_text") or not PAY_LABEL.search(button.text):
            raise ValueError("checkout requires the visible label of a submit or pay button")
        if len(details["address_hint"]) > 32 or re.search(r"\d{5,}", details["address_hint"]):
            raise ValueError("use a shortened delivery address without contact details")
        return cls(**details, button=button)

    def document(self):
        return {"merchant": self.merchant, "item": self.item, "options": self.options,
                "amount": self.amount, "address_hint": self.address_hint, "button": self.button.document()}


def tap_position(observation, action):
    if action.kind == "tap_text":
        matches = select_elements(observation.elements, action.text)
        matches = tuple(item for item in matches if item.enabled)
        if matches:
            return next((item for item in matches if item.clickable), matches[0]).center
    if action.kind == "tap" and 0 <= action.x < observation.width and 0 <= action.y < observation.height:
        return action.x, action.y
    return None


def checkout_visible(observation):
    return any(CHECKOUT_PAGE.search(node.label) or PAY_LABEL.search(node.label)
               for node in observation.elements if node.label)


def checkout_boundary(observation, action):
    """Use the label of the actual tap target, including labeled coordinate taps."""
    if action.kind == "key":
        return action.key.upper() in {"ENTER", "DPAD_CENTER"} and checkout_visible(observation)
    if action.kind not in ("tap", "tap_text"):
        return False
    if PAY_LABEL.search(action.text) or PAY_LABEL.search(action.reason):
        return True
    position = tap_position(observation, action)
    if position is None:
        return action.kind == "tap" and checkout_visible(observation)
    x, y = position
    for node in observation.elements:
        left, top, right, bottom = node.bounds
        if left <= x < right and top <= y < bottom:
            if PAY_LABEL.search(node.label) or (node.label in ("确认", "确定", "支付", "付款")
                                                and checkout_visible(observation)):
                return True
    return False


def meituan_express_at(observation, x, y):
    return any(node.package == MEITUAN_PACKAGE and node.enabled
               and node.label == MEITUAN_EXPRESS_PAYMENT
               and node.bounds[0] <= x < node.bounds[2] and node.bounds[1] <= y < node.bounds[3]
               for node in observation.elements)


def meituan_cutlery_action(observation, *, choose, needs_cutlery):
    """Resolve only controls in the cutlery sheet opened by express payment."""
    nodes = [node for node in observation.elements if node.package == MEITUAN_PACKAGE]
    if not any("是否需要餐具" in node.label or "请选择餐具数量" in node.label for node in nodes):
        return None
    options = [node for node in nodes if node.label in ("需要餐具", "无需餐具")]
    if {node.label for node in options} != {"需要餐具", "无需餐具"}:
        return None
    bottom = max(node.bounds[3] for node in options)
    submit = {node.center for node in nodes if node.label == MEITUAN_CUTLERY_SUBMIT
              and node.enabled and node.bounds[1] >= bottom}
    if len(submit) == 1:
        x, y = submit.pop()
        return PhoneAction("tap", text=MEITUAN_CUTLERY_SUBMIT, x=x, y=y)
    if choose and not submit and any(node.label == "保存" and node.bounds[1] >= bottom for node in nodes):
        label = "需要餐具" if needs_cutlery else "无需餐具"
        positions = {node.center for node in options if node.label == label and node.enabled}
        if len(positions) == 1:
            x, y = positions.pop()
            return PhoneAction("tap", text=label, x=x, y=y)
    return None


def meituan_payment_result(observation):
    return next((node.label for node in observation.elements if node.package == MEITUAN_PACKAGE
                 and node.label in MEITUAN_PAID_LABELS), None)


def checkout_frame(observation, *, form=False):
    """Bind approval to the checkout pixels, excluding a recognized Meituan banner."""
    with Image.open(BytesIO(observation.screenshot)) as image:
        width, height = image.size
        top = height // 36
        if form:
            nodes = [node for node in observation.elements if node.package == MEITUAN_PACKAGE]
            tabs = [node for node in nodes if node.label in ("外卖配送", "到店自取")]
            if {node.label for node in tabs} == {"外卖配送", "到店自取"}:
                tab_top = min(node.bounds[1] for node in tabs)
                tab_bottom = max(node.bounds[3] for node in tabs)
                if any(PAY_LABEL.search(node.label) and node.bounds[1] >= tab_bottom for node in nodes):
                    # The full-width delivery tabs start the checkout sheet. Its
                    # rotating promotional banner lives outside that container.
                    top = max((node.bounds[1] for node in nodes
                               if node.class_name == "android.widget.FrameLayout"
                               and node.bounds[0] == 0 and node.bounds[2] == width
                               and top < node.bounds[1] <= tab_top <= tab_bottom <= node.bounds[3]),
                              default=top)
        content = image.crop((0, top, width, height)).convert("RGB")
        geometry = f"{width}x{height}:{top}:".encode("ascii") if form else b""
        return hashlib.sha256(geometry + content.tobytes()).hexdigest()


def checkout_screenshot(png):
    """Prepare the exact observed checkout screen for the private image sender."""
    with Image.open(BytesIO(png)) as image:
        if image.format != "PNG":
            raise ValueError("checkout screenshot must come from the ADB PNG capture")
        width, height = image.size
        if (len(png) <= MAX_IMAGE_BYTES and max(width, height) <= 8192
                and width * height <= MAX_IMAGE_PIXELS):
            content, mime_type = png, "image/png"
        else:
            resized = image.convert("RGB")
            resized.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
            width, height = resized.size
            buffer = BytesIO()
            resized.save(buffer, "JPEG", quality=90, subsampling=0)
            content, mime_type = buffer.getvalue(), "image/jpeg"
    descriptor = ImageDescriptor(0, mime_type, width, height, len(content),
                                 hashlib.sha256(content).hexdigest())
    return CapturedImage(descriptor, content)
