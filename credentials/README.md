# 加密的渠道凭据

`miller-penguin.age` 使用 [age](https://github.com/FiloSottile/age) 加密，仅包含以下四项的 JSON 对象：

- `MILLERPAY_PHONE`
- `MILLERPAY_PASSWORD`
- `PENGUINPAY_PHONE`
- `PENGUINPAY_PASSWORD`

仓库为公开仓库，凭据原值及解密密钥仅保存在本地。当前本机的密钥路径为 `.local-secrets/miller-penguin.agekey`，该目录已被 Git 忽略。请将密钥另行安全备份；仅凭仓库中的密文无法恢复凭据。

在项目根目录使用 age 解密为独立的本地 JSON 文件：

```bash
mkdir -p .local-secrets
chmod 700 .local-secrets
umask 077
age --decrypt --identity .local-secrets/miller-penguin.agekey \
  --output .local-secrets/miller-penguin.credentials.json \
  credentials/miller-penguin.age
```

当前本机也可将命令中的 `age` 换成 `.local-secrets/age-tools/age`。解密后将所需字段填入现有 `.env`，保留其他渠道配置。应用仍从 `.env` 读取账号密码，不会自动读取此密文。
