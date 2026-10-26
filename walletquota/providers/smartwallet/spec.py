from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


SMARTWALLET_SPEC = ChannelSpec(
    name="smartwallet",
    display_name="SmartWallet",
    api_label="SmartWallet",
    api_base="https://api.smartwallet-app.com/app",
    web_origin="https://app-web.smartwallet-app.com",
    user_agent="WalletQuota/smartwallet/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
