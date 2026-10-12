from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
from treys import Card, Evaluator
import string
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")
# 内存存储房间数据
rooms = {}
evaluator = Evaluator()
# 生成6位房间号
def generate_room_id():
    while True:
        room_id = ''.join(random.choices(string.digits, k=6))
        if room_id not in rooms:
            return room_id
# 初始化玩家数据
def init_player(sid, nickname):
    return {
        'sid': sid,
        'nickname': nickname,
        'chips': 1000,
        'hand': [],
        'ready': False,
        'folded': False,
        'current_bet': 0,
        'all_in': False,
        'is_acting': False
    }
# 初始化房间数据
def init_room(room_id, host_sid, host_nickname):
    player = init_player(host_sid, host_nickname)
    return {
        'id': room_id,
        'players': [player],
        'game_state': 'waiting',  # waiting, preflop, flop, turn, river, showdown
        'community_cards': [],
        'pot': 0,
        'current_bet': 0,
        'small_blind': 10,
        'big_blind': 20,
        'dealer_pos': 0,
        'current_player_pos': 0,
        'current_round_acted': set(),  # 当前下注轮次，已经行动过的玩家位置
        'deck': [],
        'min_raise': 20
    }
# 洗牌
def shuffle_deck():
    suits = ['s', 'h', 'd', 'c']
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
    deck = []
    for suit in suits:
        for rank in ranks:
            card = Card.new(rank + suit)
            deck.append(card)
    random.shuffle(deck)
    return deck
# 发牌
def deal_hole_cards(room):
    deck = room['deck']
    for player in room['players']:
        player['hand'] = [deck.pop(), deck.pop()]
        player['folded'] = False
        player['current_bet'] = 0
        player['all_in'] = False
# 发公共牌
def deal_community_cards(room, count):
    deck = room['deck']
    for _ in range(count):
        room['community_cards'].append(deck.pop())
# 处理盲注
def post_blinds(room):
    players = room['players']
    n = len(players)
    if n < 2:
        return
    
    # 计算小盲和大盲的位置
    if n == 2:
        # 两个人的时候，小盲是庄家，大盲是另一个
        small_pos = room['dealer_pos']
        big_pos = 1 - room['dealer_pos']
    else:
        # 多人的时候，小盲是庄家下一个，大盲是小盲下一个
        small_pos = (room['dealer_pos'] + 1) % n
        big_pos = (room['dealer_pos'] + 2) % n
    
    # 扣小盲
    small_player = players[small_pos]
    small_amount = min(small_player['chips'], room['small_blind'])
    small_player['chips'] -= small_amount
    small_player['current_bet'] = small_amount
    room['pot'] += small_amount
    
    # 扣大盲
    big_player = players[big_pos]
    big_amount = min(big_player['chips'], room['big_blind'])
    big_player['chips'] -= big_amount
    big_player['current_bet'] = big_amount
    room['pot'] += big_amount
    
    room['current_bet'] = room['big_blind']
    # 当前行动玩家是大盲下一个
    if n == 2:
        # 两个人的时候，preflop 小盲（庄家）先行动
        room['current_player_pos'] = small_pos
    else:
        # 多人的时候，preflop 大盲下一个先行动
        room['current_player_pos'] = (big_pos + 1) % n
    
    # 重置preflop轮的行动标记，小盲和大盲还没行动，不要提前标记
    room['current_round_acted'] = set()
    room['min_raise'] = room['big_blind']
# 开始游戏
def start_game(room):
    # 重置游戏状态
    room['game_state'] = 'preflop'
    room['community_cards'] = []
    room['pot'] = 0
    room['current_bet'] = 0
    # 洗牌
    room['deck'] = shuffle_deck()
    # 发底牌
    deal_hole_cards(room)
    # 扣盲注
    post_blinds(room)
    # 设置当前行动玩家
    players = room['players']
    players[room['current_player_pos']]['is_acting'] = True
    # 标记第一个玩家已经行动
    room['current_round_acted'].add(room['current_player_pos'])
    # 广播游戏开始
    broadcast_room_state(room)
# 推进到下一个游戏阶段
def next_round(room):
    players = room['players']
    n = len(players)
    # 重置玩家当前下注
    for p in players:
        p['current_bet'] = 0
        p['is_acting'] = False
    room['current_bet'] = 0
    room['min_raise'] = room['big_blind']
    # 重置当前轮次的行动标记
    room['current_round_acted'] = set()
    
    # 计算小盲的位置，postflop 都是从小盲开始行动
    if n == 2:
        small_pos = room['dealer_pos']
    else:
        small_pos = (room['dealer_pos'] + 1) % n
    
    # 移动dealer
    room['dealer_pos'] = (room['dealer_pos'] + 1) % n
    
    if room['game_state'] == 'preflop':
        # 翻牌，发3张公共牌
        room['game_state'] = 'flop'
        deal_community_cards(room, 3)
        # 从小盲开始行动
        room['current_player_pos'] = small_pos
    elif room['game_state'] == 'flop':
        # 转牌，发1张
        room['game_state'] = 'turn'
        deal_community_cards(room, 1)
        room['current_player_pos'] = small_pos
    elif room['game_state'] == 'turn':
        # 河牌，发1张
        room['game_state'] = 'river'
        deal_community_cards(room, 1)
        room['current_player_pos'] = small_pos
    elif room['game_state'] == 'river':
        # 结算
        room['game_state'] = 'showdown'
        settle_game(room)
        return
    
    # 标记第一个玩家已经行动
    room['current_round_acted'].add(room['current_player_pos'])
    # 设置当前行动玩家
    players[room['current_player_pos']]['is_acting'] = True
    broadcast_room_state(room)
# 结算游戏
def settle_game(room):
    players = room['players']
    community = room['community_cards']
    
    # 收集未弃牌的玩家
    active_players = [p for p in players if not p['folded']]
    if len(active_players) == 1:
        # 只有一个玩家，直接赢走底池
        winner = active_players[0]
        winner['chips'] += room['pot']
    else:
        # 评估每个玩家的牌型
        player_scores = {}
        for p in active_players:
            hand = p['hand']
            score = evaluator.evaluate(community, hand)
            player_scores[p['sid']] = score
        
        # 找到最小的score（因为treys的score越小牌越大）
        min_score = min(player_scores.values())
        winners = [p for p in active_players if player_scores[p['sid']] == min_score]
        
        # 分配底池
        pot = room['pot']
        win_amount = pot // len(winners)
        for winner in winners:
            winner['chips'] += win_amount
    
    # 游戏结束，回到等待状态
    room['game_state'] = 'waiting'
    for p in players:
        p['ready'] = False
        p['is_acting'] = False
    
    broadcast_room_state(room)
# 处理玩家行动
def process_action(room, player, action, raise_amount=0):
    players = room['players']
    current_pos = room['current_player_pos']
    old_bet = room['current_bet']
    
    # 重置当前玩家的行动标记
    player['is_acting'] = False
    
    if action == 'fold':
        player['folded'] = True
    elif action == 'call':
        # 跟注，补全当前下注
        call_amount = room['current_bet'] - player['current_bet']
        call_amount = min(call_amount, player['chips'])
        player['chips'] -= call_amount
        player['current_bet'] += call_amount
        room['pot'] += call_amount
        # 如果筹码没了，就是全下
        if player['chips'] == 0:
            player['all_in'] = True
    elif action == 'raise':
        # 加注
        total_raise = raise_amount + (room['current_bet'] - player['current_bet'])
        total_raise = min(total_raise, player['chips'])
        player['chips'] -= total_raise
        room['pot'] += total_raise
        # 更新当前下注和最小加注
        old_bet = room['current_bet']
        room['current_bet'] = player['current_bet'] + total_raise
        room['min_raise'] = room['current_bet'] - old_bet
        player['current_bet'] = room['current_bet']
        # 如果筹码没了，全下
        if player['chips'] == 0:
            player['all_in'] = True
        # 加注了，重置当前轮次的行动标记，因为大家需要重新行动
        room['current_round_acted'] = set()
    elif action == 'allin':
        # 全下
        allin_amount = player['chips']
        player['current_bet'] += allin_amount
        room['pot'] += allin_amount
        player['chips'] = 0
        player['all_in'] = True
        # 如果全下的金额超过当前下注，更新当前下注
        if player['current_bet'] > room['current_bet']:
            old_bet = room['current_bet']
            room['current_bet'] = player['current_bet']
            room['min_raise'] = room['current_bet'] - old_bet
            # 全下超过了当前下注，重置行动标记
            room['current_round_acted'] = set()
    
    # 标记当前玩家已经行动过
    room['current_round_acted'].add(current_pos)
    
    # 找到下一个行动玩家
    next_pos = current_pos
    while True:
        next_pos = (next_pos + 1) % len(players)
        next_player = players[next_pos]
        # 跳过弃牌的和全下的
        if not next_player['folded'] and not next_player['all_in']:
            break
        # 如果绕了一圈回到自己，说明所有玩家都处理完了
        if next_pos == current_pos:
            # 进入下一轮
            next_round(room)
            return
    
    # 检查所有活跃玩家是否都已经行动过，并且下注都相等
    active_players = [i for i, p in enumerate(players) if not p['folded'] and not p['all_in']]
    all_acted = all(pos in room['current_round_acted'] for pos in active_players)
    all_bet_equal = True
    for p in players:
        if not p['folded'] and not p['all_in']:
            if p['current_bet'] != room['current_bet']:
                all_bet_equal = False
                break
    
    # 如果所有玩家都行动过了，并且下注都相等，进入下一轮
    if all_acted and all_bet_equal:
        next_round(room)
        return
    
    # 否则，设置下一个玩家行动
    players[next_pos]['is_acting'] = True
    room['current_player_pos'] = next_pos
    broadcast_room_state(room)
# 广播房间状态给所有玩家
def broadcast_room_state(room):
    # 处理玩家数据，隐藏其他玩家的手牌，默认所有玩家的hand都是空的
    players_data = []
    for p in room['players']:
        player_data = {
            'sid': p['sid'],
            'nickname': p['nickname'],
            'chips': p['chips'],
            'ready': p['ready'],
            'folded': p['folded'],
            'current_bet': p['current_bet'],
            'all_in': p['all_in'],
            'is_acting': p['is_acting'],
            # 其他玩家的手牌，默认隐藏，不发送
            'hand': []
        }
        players_data.append(player_data)
    
    room_data = {
        'room_id': room['id'],
        'players': players_data,
        'game_state': room['game_state'],
        'community_cards': room['community_cards'],
        'pot': room['pot'],
        'current_bet': room['current_bet'],
        'small_blind': room['small_blind'],
        'big_blind': room['big_blind']
    }
    
    # 给每个玩家单独发送，只把自己的手牌加上，其他玩家的hand还是空的
    for i, p in enumerate(room['players']):
        # 复制玩家数据，只给自己的手牌加上
        personal_players = players_data.copy()
        personal_players[i] = personal_players[i].copy()
        personal_players[i]['hand'] = p['hand']
        personal_room = room_data.copy()
        personal_room['players'] = personal_players
        emit('room_state', personal_room, to=p['sid'])
# 前端页面路由
@app.route('/')
def index():
    return render_template('index.html')
# SocketIO事件
@socketio.on('create_room')
def handle_create_room(data):
    nickname = data['nickname']
    sid = request.sid
    room_id = generate_room_id()
    # 创建房间
    rooms[room_id] = init_room(room_id, sid, nickname)
    # 玩家加入房间
    join_room(room_id)
    # 广播状态
    broadcast_room_state(rooms[room_id])
    # 告诉客户端房间号
    emit('room_created', {'room_id': room_id})
@socketio.on('join_room')
def handle_join_room(data):
    room_id = data['room_id']
    nickname = data['nickname']
    sid = request.sid
    
    if room_id not in rooms:
        emit('error', {'message': '房间不存在'})
        return
    
    room = rooms[room_id]
    if len(room['players']) >= 4:
        emit('error', {'message': '房间已满，最多4人'})
        return
    
    # 检查是否已经在房间里了
    for p in room['players']:
        if p['sid'] == sid:
            emit('error', {'message': '你已经在房间里了'})
            return
    
    # 添加玩家
    player = init_player(sid, nickname)
    room['players'].append(player)
    join_room(room_id)
    # 广播状态
    broadcast_room_state(room)
@socketio.on('player_ready')
def handle_player_ready():
    sid = request.sid
    # 找到玩家所在的房间
    room = None
    for r in rooms.values():
        for p in r['players']:
            if p['sid'] == sid:
                room = r
                break
        if room:
            break
    
    if not room:
        emit('error', {'message': '你不在任何房间'})
        return
    
    # 找到玩家
    player = next(p for p in room['players'] if p['sid'] == sid)
    player['ready'] = True
    
    # 检查所有玩家是否都准备了
    all_ready = all(p['ready'] for p in room['players'])
    if all_ready and len(room['players']) >= 2:
        # 开始游戏
        start_game(room)
    else:
        broadcast_room_state(room)
@socketio.on('player_action')
def handle_player_action(data):
    sid = request.sid
    action = data['action']
    raise_amount = data.get('raise_amount', 0)
    
    # 找到房间和玩家
    room = None
    player = None
    for r in rooms.values():
        for p in r['players']:
            if p['sid'] == sid:
                room = r
                player = p
                break
        if room:
            break
    
    if not room or not player:
        emit('error', {'message': '你不在任何房间'})
        return
    
    if not player['is_acting']:
        emit('error', {'message': '还没到你的行动回合'})
        return
    
    # 处理行动
    process_action(room, player, action, raise_amount)
@socketio.on('leave_room')
def handle_leave_room():
    sid = request.sid
    # 找到房间
    room = None
    for r in rooms.values():
        for p in r['players']:
            if p['sid'] == sid:
                room = r
                break
        if room:
            break
    
    if not room:
        return
    
    # 移除玩家
    room['players'] = [p for p in room['players'] if p['sid'] != sid]
    leave_room(room['id'])
    
    # 如果房间没人了，删除房间
    if len(room['players']) == 0:
        del rooms[room['id']]
    else:
        # 否则广播状态
        broadcast_room_state(room)
@socketio.on('disconnect')
def handle_disconnect(reason):
    sid = request.sid
    # 找到玩家所在的房间
    room = None
    for r in rooms.values():
        for p in r['players']:
            if p['sid'] == sid:
                room = r
                break
        if room:
            break
    
    if not room:
        return
    
    # 移除玩家
    room['players'] = [p for p in room['players'] if p['sid'] != sid]
    leave_room(room['id'])
    
    # 如果房间没人了，删除房间
    if len(room['players']) == 0:
        del rooms[room['id']]
    else:
        # 否则，如果游戏在进行中，结束游戏回到等待
        if room['game_state'] != 'waiting':
            room['game_state'] = 'waiting'
            for p in room['players']:
                p['ready'] = False
                p['is_acting'] = False
        broadcast_room_state(room)
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
