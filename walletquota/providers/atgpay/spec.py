from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


ATGPAY_SPEC = ChannelSpec(
    name="atgpay",
    display_name="ATG Pay",
    api_label="ATG",
    api_base="https://api.atg-game.com/app",
    web_origin="https://app-web.atg-game.com",
    user_agent="ATGPoolReport/1.0",
    default_sort_type=1,
    default_order_type=4,
    allowed_paths=READ_ONLY_PATHS,
)
