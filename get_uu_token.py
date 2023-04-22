import uuyoupinapi

print('本程序将会引导你获取悠悠有品的token 请注意：本程序不会自动帮你将获取到的token填入config.json中，请自行填入')
uuyoupinapi.UUAccount.get_token_automatically()
input('按回车键退出')
