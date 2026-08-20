from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


PENGUINPAY_SPEC = ChannelSpec(
    name="penguinpay",
    display_name="PenguinPay",
    api_label="PenguinPay",
    api_base="https://api.penguinpay-app.com/app",
    web_origin="https://app-web.penguinpay-app.com",
    user_agent="WalletQuota/penguinpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
