from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLERPAY_SPEC = ChannelSpec(
    name="millerpay",
    display_name="MillerPay",
    api_label="MillerPay",
    api_base="https://api.millerpay-app.com/app",
    web_origin="https://app-web.millerpay-app.com",
    user_agent="WalletQuota/millerpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
