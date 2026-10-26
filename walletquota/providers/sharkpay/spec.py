from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


SHARKPAY_SPEC = ChannelSpec(
    name="sharkpay",
    display_name="SharkPay",
    api_label="SharkPay",
    api_base="https://api.sharkpay-app.com/app",
    web_origin="https://app-web.sharkpay-app.com",
    user_agent="WalletQuota/sharkpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
