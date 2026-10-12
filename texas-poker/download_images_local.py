
import requests
import os

# 图片的目标目录
target_dir = 'static/images'
os.makedirs(target_dir, exist_ok=True)

# 所有的牌
ranks = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
suits = ['S', 'H', 'D', 'C']

# 用ghproxy代理下载，你本地可以访问
base_url = 'https://ghproxy.com/https://raw.githubusercontent.com/hayeah/playing-cards-assets/master/png/'

print("开始下载扑克牌图片到本地static/images文件夹...")
for rank in ranks:
    for suit in suits:
        filename = f'{rank}{suit}.png'
        url = base_url + filename
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            with open(os.path.join(target_dir, filename), 'wb') as f:
                f.write(resp.content)
            print(f'成功下载: {filename}')
        except Exception as e:
            print(f'下载失败 {filename}: {e}')

# 下载背面
try:
    resp = requests.get(base_url + 'back.png', timeout=10)
    resp.raise_for_status()
    with open(os.path.join(target_dir, 'back.png'), 'wb') as f:
        f.write(resp.content)
    print('成功下载: back.png')
except Exception as e:
    print(f'下载失败 back.png: {e}')

print("所有图片下载完成！现在你可以本地运行游戏，图片就可以正常显示了。")
