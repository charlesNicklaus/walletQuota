from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


SHOWPAY_SPEC = ChannelSpec(
    name="showpay",
    display_name="ShowPay",
    api_label="ShowPay",
    api_base="https://api.showpayweb.com/app",
    web_origin="https://app-web.showpayweb.com",
    user_agent="WalletQuota/showpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
