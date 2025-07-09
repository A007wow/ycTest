from flask import Flask, render_template, request, redirect, url_for, session
import os
import random
import re
from docx import Document
import sqlite3
import json
import uuid
from flask_session import Session
from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key_please_change') 

# Flask-Session 配置
SESSION_FILE_DIR = os.path.join(os.path.dirname(__file__), 'flask_session')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = SESSION_FILE_DIR
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True

Session(app)

def remove_bracket_content(text):
    # 删除题干中的括号及其内容（全角中文括号）
    return re.sub(r'（[^（）]*）', '', text)

def remove_last_bracket_content(text):
    # 只去掉最后一个全角括号及内容
    return re.sub(r'（[^（）]*）$', '', text)

def remove_last_bracket_content_keep_bracket(text):
    # 只去掉最后一个全角括号内的内容，保留括号本身
    return re.sub(r'（[^（）]*）$', '（）', text)

def remove_first_bracket_content_keep_bracket(text):
    # 只去掉第一个全角括号内的内容，保留括号本身
    return re.sub(r'（[^（）]*）', '（）', text, count=1)

def parse_option(option_text):
    # 解析选项，分离字母和内容，如 "A. 内容" 或 "A 内容"
    m = re.match(r'^([A-Z])\.?\s*(.*)', option_text)
    if m:
        return {'label': m.group(1), 'text': m.group(2)}
    else:
        return {'label': '', 'text': option_text}

def is_judge_question(ans, opts):
    # 判断题：括号内内容不是字母
    if ans is not None and not re.fullmatch(r'[A-Za-z]', ans):
        return True
    return False

def normalize_judge_answer(ans):
    if ans is None:
        return ''
    ans = str(ans).strip()
    if ans in ['正确', '√', '对', '是', 'yes', 'Y', 'y', 'True', 'true']:
        return '√'
    elif ans in ['错误', '×', '错', '否', 'no', 'N', 'n', 'False', 'false']:
        return '×'
    return ans

# 题目解析函数
def parse_questions_from_docx(files_dir):
    questions = []
    for filename in os.listdir(files_dir):
        if filename.endswith('.docx'):
            filepath = os.path.join(files_dir, filename)
            doc = Document(filepath)
            current_question = None
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                # 判断是否为题干
                if text and text[0].isdigit() and '.' in text:
                    # 解析答案（括号内内容）
                    ans = None
                    if '（' in text and '）' in text:
                        ans = text.split('（')[-1].split('）')[0].strip()
                    # 题干去除第一个括号内内容，保留括号本身
                    stem_no_ans = remove_first_bracket_content_keep_bracket(text)
                    current_question = {
                        'stem': stem_no_ans,
                        'options': [],
                        'answer': ans,
                        'type': 'unknown',
                    }
                    questions.append(current_question)
                elif current_question:
                    # 检查是否为一行多个选项
                    multi_opts = re.findall(r'（([A-Z])）([^（]*)', text)
                    if multi_opts and len(multi_opts) > 1:
                        for label, content in multi_opts:
                            current_question['options'].append({'label': label, 'text': content.strip()})
                    else:
                        current_question['options'].append(text)
            # 判断题型和处理选项
            for q in questions:
                opts = [o['text'].strip() if isinstance(o, dict) and 'text' in o else str(o).strip() for o in q['options']]
                if is_judge_question(q['answer'], q['options']):
                    q['type'] = 'judge'
                    q['options'] = [{'label': 'A', 'text': '正确'}, {'label': 'B', 'text': '错误'}]
                else:
                    q['type'] = 'choice'
                    q['options'] = [parse_option(opt) if not isinstance(opt, dict) else opt for opt in q['options']]
    return questions

@app.route('/', methods=['GET'])
def index():
    # 进入首页时清空模式相关session，防止混用
    session.pop('quiz_ids', None)
    session.pop('review_wrong_ids', None)
    session.pop('user_answers', None)
    session.pop('quiz_answers', None)
    session.pop('review_answers', None)
    session.pop('current_mode', None)
    session.pop('current_quiz_id', None)
    session.pop('current_review_id', None)
    # 优化：清理 quiz_sessions 只保留 token/id，不存 questions
    session.pop('quiz_sessions', None)
    return redirect(url_for('quiz'))

@app.route('/result', methods=['GET'])
def result():
    quiz_token = request.args.get('quiz_token')
    if quiz_token:
        quiz_sessions = session.get('quiz_sessions', {})
        if quiz_token in quiz_sessions:
            ids = quiz_sessions[quiz_token].get('quiz_ids', [])
            user_answers = session.get('quiz_answers', {})
        else:
            ids = []
            user_answers = {}
    else:
        id_list_str = request.args.get('id_list')
        if id_list_str:
            ids = id_list_str.split(',')
        else:
            # 根据当前模式获取id列表
            current_mode = session.get('current_mode', 'quiz')
            if current_mode == 'review_wrong':
                ids = session.get('review_wrong_ids', [])
            else:
                # quiz模式下，从quiz_sessions中获取当前token对应的ids
                current_quiz_id = session.get('current_quiz_id')
                quiz_sessions = session.get('quiz_sessions', {})
                if current_quiz_id and current_quiz_id in quiz_sessions:
                    ids = quiz_sessions[current_quiz_id].get('quiz_ids', [])
                else:
                    ids = []
        # 根据当前模式获取用户答案
        current_mode = session.get('current_mode', 'quiz')
        if current_mode == 'review_wrong':
            user_answers = session.get('review_answers', {})
        else:
            user_answers = session.get('quiz_answers', session.get('user_answers', {}))
    
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    questions = []
    if ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?']*len(ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),
                'qid': str(row['qid'])
            })
    id2q = {str(q['id']): q for q in questions}
    quiz_questions = [id2q[str(id)] for id in ids if str(id) in id2q]
    results = []
    for q in quiz_questions:
        qid = str(q['id'])
        user_ans = user_answers.get(qid)
        if q['type'] == 'multi':
            correct = set(user_ans) == set(q['answer']) if user_ans else False
        elif q['type'] == 'choice':
            correct = user_ans == q['answer']
        elif q['type'] == 'judge':
            ans_db = q['answer']
            if ans_db == 'A':
                ans_db = '√'
            elif ans_db == 'B':
                ans_db = '×'
            correct = normalize_judge_answer(user_ans) == normalize_judge_answer(ans_db)
        else:
            correct = (user_ans == q['answer'])
        results.append({'question': q, 'user_answer': user_ans, 'correct': correct})
    # 获取当前模式
    current_mode = session.get('current_mode', 'quiz')
    is_review_mode = current_mode == 'review_wrong'
    
    if is_review_mode:
        # 复习模式：显示错题复习统计
        wrong_questions = session.get('wrong_questions', [])
        total_wrong_questions = len(wrong_questions)
        current_review_count = len(quiz_questions)
        # 重新计算剩余错题数（最新）
        remaining_wrong_count = len(session.get('wrong_questions', []))
        
        return render_template('result.html', 
                             results=results, 
                             total=len(results), 
                             correct=correct_count, 
                             wrong=len(results)-correct_count,
                             total_questions=total_wrong_questions,  # 总错题数
                             done_count=current_review_count,  # 本次复习题目数
                             correct_count=remaining_wrong_count,  # 剩余错题数
                             wrong_count=remaining_wrong_count,    # 只显示最新剩余错题数
                             review_wrong_mode=True,
                             id_list=ids, 
                             quiz_token=quiz_token)
    else:
        # quiz模式：显示总体刷题统计
        total_questions = session.get('total_questions', 0)
        done_count = session.get('done_count', 0)
        correct_count = session.get('correct_count', 0)
        wrong_count = len(session.get('wrong_questions', []))
        
        return render_template('result.html', 
                             results=results, 
                             total=len(results), 
                             correct=correct_count, 
                             wrong=len(results)-correct_count,
                             total_questions=total_questions,
                             done_count=done_count,
                             wrong_count=wrong_count,
                             review_wrong_mode=False,
                             id_list=ids, 
                             quiz_token=quiz_token)

@app.route('/review_wrong_page', methods=['GET', 'POST'])
def review_wrong_page():
    # 切换模式时清空quiz_ids，防止混用
    session.pop('quiz_ids', None)
    session.pop('user_answers', None)
    session.pop('quiz_answers', None)
    session.pop('current_mode', None)
    session.pop('current_quiz_id', None)
    session.pop('current_review_id', None)
    # 优化：清理 quiz_sessions 只保留 token/id，不存 questions
    session.pop('quiz_sessions', None)
    
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 从错题列表中获取题目ID
    wrong_ids = session.get('wrong_questions', [])
    questions = []
    if wrong_ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?']*len(wrong_ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', wrong_ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),  # 使用id作为主要标识符
                'qid': str(row['qid'])  # 保留qid用于表单字段名
            })
        # 随机打乱题目顺序，取前10题
        random.shuffle(questions)
        questions = questions[:10]
    
    conn.close()
    session['review_wrong_ids'] = [q['id'] for q in questions]  # 只存id列表
    
    # 获取错题相关统计信息
    wrong_questions = session.get('wrong_questions', [])
    total_wrong_questions = len(wrong_questions)  # 总错题数
    current_review_count = len(questions)  # 本次复习的题目数
    remaining_wrong_count = total_wrong_questions  # 剩余错题数（初始等于总错题数）
    
    # 明确设置当前模式
    session['current_mode'] = 'review_wrong'
    session['current_review_id'] = str(uuid.uuid4())
    
    return render_template('review_wrong.html', 
                         questions=questions, 
                         total=len(questions), 
                         current=1, 
                         total_questions=total_wrong_questions,  # 总错题数
                         done_count=current_review_count,  # 本次复习题目数
                         correct_count=remaining_wrong_count,  # 剩余错题数
                         review_wrong_mode=True)

@app.route('/review_wrong', methods=['GET'])
def review_wrong():
    # 清空quiz相关session，确保切换到review_wrong模式
    session.pop('quiz_ids', None)
    session.pop('quiz_answers', None)
    session.pop('current_mode', None)
    session.pop('current_quiz_id', None)
    return redirect(url_for('review_wrong_page'))

@app.route('/sequential', methods=['GET'])
def sequential():
    # 切换模式时清空quiz_ids，防止混用
    session.pop('quiz_ids', None)
    session.pop('review_wrong_ids', None)
    session.pop('user_answers', None)
    session.pop('quiz_answers', None)
    session.pop('review_answers', None)
    session.pop('current_mode', None)
    session.pop('current_quiz_id', None)
    session.pop('current_review_id', None)
    # 优化：清理 quiz_sessions 只保留 token/id，不存 questions
    session.pop('quiz_sessions', None)
    
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 获取所有题目id
    c.execute('SELECT id FROM merged_questions')
    all_ids = [row['id'] for row in c.fetchall()]
    done_ids = set(session.get('done_ids', []))
    left_ids = list(set(all_ids) - done_ids)
    
    # 随机抽10题
    pick_ids = []
    if left_ids:
        random.shuffle(left_ids)
        pick_ids = left_ids[:10]
    
    questions = []
    if pick_ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?'] * len(pick_ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', pick_ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),  # 使用id作为主要标识符
                'qid': str(row['qid'])  # 保留qid用于表单字段名
            })
    
    conn.close()
    
    quiz_ids = [q['id'] for q in questions]
    print('渲染前 questions ids:', [q['id'] for q in questions])
    print('渲染前 quiz_ids:', quiz_ids)
    
    token = str(uuid.uuid4())
    quiz_sessions = session.get('quiz_sessions', {})
    if len(quiz_sessions) >= 10:
        for old_token in list(quiz_sessions.keys())[:-9]:
            quiz_sessions.pop(old_token)
    quiz_sessions[token] = {
        'quiz_ids': quiz_ids,  # 改为quiz_ids
        'mode': 'sequential',
    }
    session['quiz_sessions'] = quiz_sessions
    session.modified = True
    
    # 明确设置当前模式
    session['current_mode'] = 'quiz'
    session['current_quiz_id'] = token
    
    total_questions = session.get('total_questions', 0)
    done_count = session.get('done_count', 0)
    # 错题数应该基于wrong_questions列表的长度（唯一题号）
    wrong_count = len(session.get('wrong_questions', []))
    return render_template('quiz.html', questions=questions, total=len(questions), current=1, total_questions=total_questions, done_count=done_count, wrong_count=wrong_count, quiz_token=token)

# 修改quiz路由，保存total_questions到session
@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    if request.method == 'GET':
        # 清空review_wrong相关session，确保切换到quiz模式
        session.pop('review_wrong_ids', None)
        session.pop('review_answers', None)
        session.pop('current_review_id', None)
        
        db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 获取总题目数
        c.execute('SELECT COUNT(*) FROM merged_questions')
        total_questions = c.fetchone()[0]
        session['total_questions'] = total_questions
        
        # 获取已做过的题目ID和错题ID
        done_ids = set(session.get('done_ids', []))
        wrong_questions = session.get('wrong_questions', [])
        
        questions = []
        
        # 判断错题数量，决定抽取策略
        if len(wrong_questions) >= 3:
            # 错题数≥3时：抽取3道错题 + 7道新题
            print(f"错题数: {len(wrong_questions)}, 采用混合抽取策略")
            
            # 1. 从错题中随机抽取3道
            wrong_sample = random.sample(wrong_questions, min(3, len(wrong_questions)))
            print(f"抽取的错题ID: {wrong_sample}")
            wrong_ids_str = ','.join(['?'] * len(wrong_sample))
            c.execute(f'SELECT * FROM merged_questions WHERE id IN ({wrong_ids_str})', wrong_sample)
            wrong_questions_data = [dict(row) for row in c.fetchall()]
            print(f"错题数量: {len(wrong_questions_data)}")
            
            # 2. 从新题中随机抽取7道（排除已做题和错题）
            exclude_ids = done_ids.union(set(wrong_questions))
            print(f"排除的ID数量: {len(exclude_ids)} (已做题: {len(done_ids)}, 错题: {len(wrong_questions)})")
            if exclude_ids:
                exclude_ids_str = ','.join(['?'] * len(exclude_ids))
                c.execute(f'SELECT * FROM merged_questions WHERE id NOT IN ({exclude_ids_str}) ORDER BY RANDOM() LIMIT 7', list(exclude_ids))
            else:
                c.execute('SELECT * FROM merged_questions ORDER BY RANDOM() LIMIT 7')
            new_questions_data = [dict(row) for row in c.fetchall()]
            print(f"新题数量: {len(new_questions_data)}")
            
            # 合并题目数据
            questions_data = wrong_questions_data + new_questions_data
            
        else:
            # 错题数<3时：抽取10道新题
            print(f"错题数: {len(wrong_questions)}, 采用纯新题抽取策略")
            
            # 从新题中随机抽取10道（排除已做题）
            if done_ids:
                done_ids_str = ','.join(['?'] * len(done_ids))
                c.execute(f'SELECT * FROM merged_questions WHERE id NOT IN ({done_ids_str}) ORDER BY RANDOM() LIMIT 10', list(done_ids))
            else:
                c.execute('SELECT * FROM merged_questions ORDER BY RANDOM() LIMIT 10')
            questions_data = [dict(row) for row in c.fetchall()]
            print(f"新题数量: {len(questions_data)}")
        
        conn.close()
        
        def parse_merged_question(q):
            # 根据question_type确定题目类型
            if q['question_type'] == 'single_choice':
                qtype = 'choice'
            elif q['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif q['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            return {
                'stem': q['stem'],
                'options': json.loads(q['options']) if q['options'] else [],
                'answer': q['answer'],
                'type': qtype,
                'id': str(q['id']),  # 使用id作为主要标识符
                'qid': str(q['qid'])  # 保留qid用于表单字段名
            }
        
        questions = [parse_merged_question(q) for q in questions_data]
        random.shuffle(questions)  # 打乱题目顺序
        
        # 使用id作为主要标识
        quiz_ids = [q['id'] for q in questions]
        print('渲染前 questions ids:', [q['id'] for q in questions])
        print('渲染前 quiz_ids:', quiz_ids)
        
        token = str(uuid.uuid4())
        quiz_sessions = session.get('quiz_sessions', {})
        if len(quiz_sessions) >= 10:
            for old_token in list(quiz_sessions.keys())[:-9]:
                quiz_sessions.pop(old_token)
        quiz_sessions[token] = {
            'quiz_ids': quiz_ids,  # 改为quiz_ids
            'mode': 'quiz',
        }
        session['quiz_sessions'] = quiz_sessions
        session.modified = True
        
        # 明确设置当前模式
        session['current_mode'] = 'quiz'
        session['current_quiz_id'] = token
        
        done_count = session.get('done_count', 0)
        # 错题数应该基于wrong_questions列表的长度（唯一题号）
        wrong_count = len(session.get('wrong_questions', []))
        return render_template('quiz.html', questions=questions, total=len(questions), current=1, total_questions=total_questions, done_count=done_count, wrong_count=wrong_count, quiz_token=token)
    else:
        return redirect(url_for('submit'))

# 修改submit路由，支持token隔离
@app.route('/submit', methods=['POST'])
def submit():
    print('request.form:', dict(request.form))
    quiz_token = request.form.get('quiz_token')
    quiz_sessions = session.get('quiz_sessions', {})
    user_answers = {}
    # token失效检测
    if not session.get('review_wrong_ids', []) and (not quiz_token or quiz_token not in quiz_sessions):
        return '本次答题会话已失效，请重新开始练习。', 400
    # 调试：打印渲染前questions ids和quiz_ids
    if quiz_token and quiz_token in quiz_sessions:
        print('submit时 quiz_token:', quiz_token)
        print('submit时 quiz_ids:', quiz_sessions[quiz_token]['quiz_ids'])
    # 优先用review_wrong_ids，否则用token查quiz_ids
    ids = session.get('review_wrong_ids', [])
    if not ids and quiz_token and quiz_token in quiz_sessions:
        ids = quiz_sessions[quiz_token]['quiz_ids']
    print('使用id列表:', ids)
    
    # 根据当前模式确定答案存储key
    current_mode = session.get('current_mode', 'quiz')
    if current_mode == 'review_wrong':
        answers_key = 'review_answers'
    else:
        answers_key = 'quiz_answers'
    
    # 从数据库查出所有题目
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    questions = []
    if ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?']*len(ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),  # 使用id作为主要标识符
                'qid': str(row['qid'])  # 保留qid用于表单字段名
            })
    # 按ids顺序排序
    id2q = {str(q['id']): q for q in questions}
    quiz_questions = [id2q[str(id)] for id in ids if str(id) in id2q]
    review_wrong_mode = bool(session.get('review_wrong_ids', []))
    
    # 调试：打印所有表单字段
    print('所有表单字段:', list(request.form.keys()))
    
    for q in quiz_questions:
        question_id = str(q['id'])  # 使用id作为表单字段名
        print(f'处理题目: id={q["id"]}, qid={q["qid"]}, type={q["type"]}')
        
        if q['type'] == 'multi':
            ans = request.form.getlist(f'q_{question_id}[]')
            user_answers[question_id] = ans if ans else None
        else:
            user_answers[question_id] = request.form.get(f'q_{question_id}')
        print('question_id:', question_id, 'type:', q['type'], 'user_ans:', user_answers[question_id])
    
    # 根据模式存储答案
    session[answers_key] = user_answers
    session['user_answers'] = user_answers  # 保持兼容性
    
    # 计算结果并更新统计
    results = []
    correct_count = 0
    # 统一错题ID为字符串
    wrong_ids = set(str(i) for i in session.get('wrong_questions', []))
    done_ids = set(str(i) for i in session.get('done_ids', []))
    recent_done = session.get('recent_done', [])
    recent_wrong = session.get('recent_wrong', [])
    
    # 判断当前模式
    current_mode = session.get('current_mode', 'quiz')
    is_review_mode = current_mode == 'review_wrong'
    
    for q in quiz_questions:
        question_id = str(q['id'])
        user_ans = user_answers.get(question_id)
        
        # 判分
        if q['type'] == 'multi':
            correct = set(user_ans) == set(q['answer']) if user_ans else False
        elif q['type'] == 'choice':
            correct = user_ans == q['answer']
        elif q['type'] == 'judge':
            ans_db = q['answer']
            if ans_db == 'A':
                ans_db = '√'
            elif ans_db == 'B':
                ans_db = '×'
            correct = normalize_judge_answer(user_ans) == normalize_judge_answer(ans_db)
        else:
            correct = (user_ans == q['answer'])
        
        # 更新统计（根据模式不同处理）
        if is_review_mode:
            # 复习模式：只管理错题列表，不更新总体统计
            if correct:
                # 做对了错题，从错题列表中删除
                wrong_ids.discard(question_id)
            else:
                # 做错了错题，确保不重复添加（错题ID唯一性）
                wrong_ids.add(question_id)
            # 复习模式不更新 done_count, correct_count, done_ids
        else:
            # quiz模式：正常更新所有统计
            if correct:
                correct_count += 1
                # 如果做对了错题，从错题列表中删除
                wrong_ids.discard(question_id)
            else:
                # 如果做错了题，添加到错题列表（确保唯一性）
                wrong_ids.add(question_id)
            # 添加到已做题列表
            done_ids.add(question_id)
        
        # 保存最近做过的20道题（含题目、选项、用户选择、正确答案）
        if not is_review_mode:
            recent_done.append({
                'stem': q['stem'],
                'options': q['options'],
                'answer': q['answer'],
                'type': q['type'],
                'id': q['id'],
                'qid': q['qid'],
                'user_answer': user_ans,
                'correct': correct
            })
            if len(recent_done) > 20:
                recent_done = recent_done[-20:]
        # 保存最近做错的20道题（含题目、选项、用户选择、正确答案）
        if not correct and not is_review_mode:
            recent_wrong.append({
                'stem': q['stem'],
                'options': q['options'],
                'answer': q['answer'],
                'type': q['type'],
                'id': q['id'],
                'qid': q['qid'],
                'user_answer': user_ans,
                'correct': correct
            })
            if len(recent_wrong) > 20:
                recent_wrong = recent_wrong[-20:]
        results.append({'question': q, 'user_answer': user_ans, 'correct': correct})
    
    # 更新session统计
    if is_review_mode:
        # 复习模式：只更新错题列表
        session['wrong_questions'] = list(wrong_ids)  # 允许错题数无限增长
    else:
        # quiz模式：更新所有统计
        done_count = len(quiz_questions)
        session['done_count'] = session.get('done_count', 0) + done_count
        session['correct_count'] = session.get('correct_count', 0) + correct_count
        session['wrong_questions'] = list(wrong_ids)  # 允许错题数无限增长
        session['done_ids'] = list(done_ids)[-100:]  # 只保留最近 100 个做题记录
        session['recent_done'] = recent_done
        session['recent_wrong'] = recent_wrong
    
    conn.close()
    # 不再存last_results，结果页动态生成
    # 计算错题数
    wrong_count = len(session.get('wrong_questions', []))
    
    return render_template('result.html', results=results, done_count=session['done_count'], correct_count=session['correct_count'], total_questions=session.get('total_questions', 0), review_wrong_mode=review_wrong_mode, wrong_count=wrong_count)

@app.route('/review/<qid>', methods=['GET', 'POST'])
def review(qid):
    # 注意：这里的qid参数实际上是题目的id，不是数据库中的qid字段
    id_list_str = request.args.get('id_list')
    if id_list_str:
        ids = id_list_str.split(',')
    else:
        # 根据当前模式获取id列表
        current_mode = session.get('current_mode', 'quiz')
        if current_mode == 'review_wrong':
            ids = session.get('review_wrong_ids', [])
        else:
            # quiz模式下，从quiz_sessions中获取当前token对应的ids
            current_quiz_id = session.get('current_quiz_id')
            quiz_sessions = session.get('quiz_sessions', {})
            if current_quiz_id and current_quiz_id in quiz_sessions:
                ids = quiz_sessions[current_quiz_id].get('quiz_ids', [])
            else:
                ids = []
    
    ids = [str(x) for x in ids]  # 强制转为str，防止类型冲突
    
    # 从数据库查出所有题目
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    questions = []
    if ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?']*len(ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),
                'qid': str(row['qid'])
            })
    
    id2q = {str(q['id']): q for q in questions}
    question = id2q.get(str(qid))  # 这里qid实际上是id
    
    # 根据当前模式获取用户答案
    current_mode = session.get('current_mode', 'quiz')
    if current_mode == 'review_wrong':
        user_answers = session.get('review_answers', {})
    else:
        user_answers = session.get('quiz_answers', session.get('user_answers', {}))
    
    user_ans = None
    if question:
        user_ans = user_answers.get(str(qid))  # 使用id获取用户答案
    
    # 判分
    correct = False
    if question and user_ans:
        if question['type'] == 'multi':
            correct = set(user_ans) == set(question['answer']) if user_ans else False
        elif question['type'] == 'choice':
            correct = user_ans == question['answer']
        elif question['type'] == 'judge':
            ans_db = question['answer']
            if ans_db == 'A':
                ans_db = '√'
            elif ans_db == 'B':
                ans_db = '×'
            correct = normalize_judge_answer(user_ans) == normalize_judge_answer(ans_db)
        else:
            correct = (user_ans == question['answer'])
    
    # 计算题号
    try:
        question_index = ids.index(str(qid)) + 1
    except ValueError:
        question_index = 1
    
    conn.close()
    return render_template(
        'review.html',
        question=question,
        user_answer=user_ans,
        qid=qid,
        correct=correct,
        question_index=question_index,
        id_list=ids,
        quiz_token=session.get('current_quiz_id')
    )

@app.route('/reset', methods=['POST'])
def reset():
    session['done_count'] = 0
    session['correct_count'] = 0
    session['wrong_questions'] = []
    session['done_ids'] = []
    # 清空模式相关数据
    session.pop('current_mode', None)
    session.pop('current_quiz_id', None)
    session.pop('current_review_id', None)
    session.pop('quiz_answers', None)
    session.pop('review_answers', None)
    session.pop('user_answers', None)
    session.pop('quiz_sessions', None)
    session.pop('review_wrong_ids', None)
    # 优化：不再存 last_results，结果页动态生成
    session.pop('last_results', None)
    return redirect(url_for('sequential'))

@app.route('/wrong_list', methods=['GET'])
def wrong_list():
    # 获取最新做错的20个id
    wrong_ids = list(reversed(session.get('wrong_questions', [])))[:20]
    db_path = os.path.join(os.path.dirname(__file__), 'questions.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    wrong_questions = []
    if wrong_ids:
        # 从merged_questions表获取题目
        marks = ','.join(['?']*len(wrong_ids))
        c.execute(f'SELECT * FROM merged_questions WHERE id IN ({marks})', wrong_ids)
        for row in c.fetchall():
            # 根据question_type确定题目类型
            if row['question_type'] == 'single_choice':
                qtype = 'choice'
            elif row['question_type'] == 'multi_choice':
                qtype = 'multi'
            elif row['question_type'] == 'judge':
                qtype = 'judge'
            else:
                qtype = 'choice'  # 默认
            
            wrong_questions.append({
                'stem': row['stem'],
                'options': json.loads(row['options']) if row['options'] else [],
                'answer': row['answer'],
                'type': qtype,
                'id': str(row['id']),  # 使用id作为主要标识符
                'qid': str(row['qid'])  # 保留qid用于表单字段名
            })
    # 获取用户做题记录
    user_answers = session.get('user_answers', {})
    # 只保留最新20个错题，按wrong_ids顺序
    id2q = {q['id']: q for q in wrong_questions}
    questions = [id2q[id] for id in wrong_ids if id in id2q]
    return render_template('wrong_list.html', questions=questions, user_answers=user_answers)

@app.route('/recent_done', methods=['GET'])
def recent_done():
    recent_done = session.get('recent_done', [])
    return render_template('recent_done.html', questions=recent_done)

@app.route('/recent_wrong_list', methods=['GET'])
def recent_wrong_list():
    recent_wrong = session.get('recent_wrong', [])
    return render_template('recent_wrong_list.html', questions=recent_wrong)

if __name__ == '__main__':
    app.run(debug=True)
