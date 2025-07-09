import os
import re
import json
import sqlite3
from docx import Document

def remove_first_bracket_content_keep_bracket(text):
    # 只去掉第一个全角括号内的内容，保留括号本身
    return re.sub(r'（[^（）]*）', '（）', text, count=1)

def parse_option(option_text):
    m = re.match(r'^([A-Z])\.?\s*(.*)', option_text)
    if m:
        return {'label': m.group(1), 'text': m.group(2)}
    else:
        return {'label': '', 'text': option_text}

def is_judge_answer(ans):
    # 判断题答案常见写法
    return ans in {'√', '×', '对', '错', '正确', '错误', '是', '否', 'yes', 'no', 'y', 'n', 'true', 'false', 'T', 'F'}

def extract_valid_bracket(content):
    # 连续查找括号，直到找到括号内容为空、全大写字母，或为判断题常见答案
    for m in re.finditer(r'（([^（）]*)）', content):
        inner = m.group(1).strip()
        if (
            inner == '' or
            all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for c in inner) or
            is_judge_answer(inner)
        ):
            return m, inner
    return None, None

def parse_questions_from_docx(files_dir):
    questions = []
    for filename in os.listdir(files_dir):
        if filename.endswith('.docx'):
            filepath = os.path.join(files_dir, filename)
            doc = Document(filepath)
            current_question = None
            reading_options = False
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                m = re.match(r'^(\d+)[.．、]\s*(.+)$', text)
                if m:
                    qid = m.group(1)
                    content = m.group(2)
                    dot_idx = min([i for i in [content.find('。'), content.find('.'), content.find('．')] if i != -1] or [-1])
                    brk_idx = content.find('（')
                    ans = ''
                    stem = ''
                    rest = ''
                    qtype = None
                    if dot_idx != -1 and brk_idx != -1:
                        before_dot = content[:dot_idx+1]
                        after_dot = content[dot_idx+1:]
                        # 只要括号内包括文字就忽略，继续找下一个
                        before_brk_match, before_brk = extract_valid_bracket(before_dot)
                        after_brk_match, after_brk = extract_valid_bracket(after_dot)
                        # 新增逻辑：句号前括号为空，句号后括号不是字母，为判断题
                        if before_brk == '' and after_brk and not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for c in after_brk):
                            stem = re.sub(r'（[^（）]*）', '（）', before_dot, count=1) + after_dot[:after_brk_match.start()] + '（）' + after_dot[after_brk_match.end():] if after_brk_match else content
                            ans = after_brk
                            qtype = 'judge'
                        # 新增逻辑：句号前括号内容为文字时，忽略这个括号
                        elif before_brk is None:
                            stem = before_dot  # 保留原文，不替换括号
                            rest = after_dot
                            ans_match, ans_val = extract_valid_bracket(rest)
                            if ans_match:
                                ans = ans_val
                            qtype = None  # 题型待后续归类
                        elif brk_idx > dot_idx:
                            # 括号在句号后，是判断题
                            stem = content[:dot_idx+1]
                            rest = content[dot_idx+1:]
                            ans_match, ans_val = extract_valid_bracket(rest)
                            if ans_match:
                                ans = ans_val
                            qtype = 'judge'
                        else:
                            # 括号在句号前，是选择题
                            stem_raw = content[:dot_idx+1]
                            rest = content[dot_idx+1:]
                            ans_match, ans_val = extract_valid_bracket(stem_raw)
                            if ans_match:
                                ans = ans_val
                                # 删去括号内内容，保留括号
                                stem = stem_raw[:ans_match.start()] + '（）' + stem_raw[ans_match.end():]
                            else:
                                stem = stem_raw
                            # 判断单选/多选
                            if len(ans) == 1:
                                qtype = 'single'
                            else:
                                qtype = 'multi'
                    elif brk_idx != -1:
                        # 没有句号但有括号，提取第一个有效括号内容为答案，并删去
                        ans_match, ans_val = extract_valid_bracket(content)
                        if ans_match:
                            ans = ans_val
                            stem = content[:ans_match.start()] + '（）' + content[ans_match.end():]
                        else:
                            stem = content
                        rest = ''
                        qtype = None
                    else:
                        # 没有句号或括号，全部为题干
                        stem = content
                        rest = ''
                        qtype = None
                    current_question = {
                        'qid': qid,
                        'stem': stem,
                        'options': [],
                        'answer': ans,
                        'qtype': qtype
                    }
                    questions.append(current_question)
                    reading_options = True
                    # 句号后如果还有括号，作为选项
                    multi_opts = re.findall(r'（([A-Z])）([^（]*)', rest)
                    if multi_opts:
                        for label, content in multi_opts:
                            current_question['options'].append({'label': label, 'text': content.strip()})
                    continue
                if reading_options and current_question:
                    # 支持多行选项
                    multi_opts = re.findall(r'（([A-Z])）([^（]*)', text)
                    if multi_opts:
                        for label, content in multi_opts:
                            current_question['options'].append({'label': label, 'text': content.strip()})
                    else:
                        # 如果遇到下一个题干，停止读取选项
                        if re.match(r'^\d+[.．、]', text):
                            reading_options = False
                        else:
                            print(f"未识别选项行: {text}")
                else:
                    print(f"未识别题干行: {text}")
    # 题型归类
    for q in questions:
        ans = q['answer']
        opts = q['options']
        if is_judge_answer(ans):
            q['qtype'] = 'judge'
            q['options'] = [{'label': 'A', 'text': '正确'}, {'label': 'B', 'text': '错误'}]
        elif opts and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for c in ans):
            q['qtype'] = 'single' if len(ans) == 1 else 'multi'
        elif not opts and ans:
            q['qtype'] = 'fill'  # 新增填空题类型
        else:
            q['qtype'] = 'unknown'

    # 打印所有名词解释/填空题
    for q in questions:
        if q['qtype'] == 'fill':
            print(f"名词解释型题目: 题号={q['qid']}, 题干={q['stem']}, 答案={q['answer']}")
    return questions

def save_to_db(judge_questions, single_choice_questions, multi_choice_questions, db_path='questions.db'):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS judge_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT,
            stem TEXT,
            options TEXT,
            answer TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS single_choice_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT,
            stem TEXT,
            options TEXT,
            answer TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS multi_choice_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT,
            stem TEXT,
            options TEXT,
            answer TEXT
        )
    ''')
    for q in judge_questions:
        c.execute(
            'INSERT INTO judge_questions (qid, stem, options, answer) VALUES (?, ?, ?, ?)',
            (q['qid'], q['stem'], json.dumps(q['options'], ensure_ascii=False), q['answer'])
        )
    for q in single_choice_questions:
        c.execute(
            'INSERT INTO single_choice_questions (qid, stem, options, answer) VALUES (?, ?, ?, ?)',
            (q['qid'], q['stem'], json.dumps(q['options'], ensure_ascii=False), q['answer'])
        )
    for q in multi_choice_questions:
        c.execute(
            'INSERT INTO multi_choice_questions (qid, stem, options, answer) VALUES (?, ?, ?, ?)',
            (q['qid'], q['stem'], json.dumps(q['options'], ensure_ascii=False), q['answer'])
        )
    conn.commit()
    conn.close()

def normalize_answer(ans):
    # 去除空格，转小写，全角转半角
    ans = ans.strip().replace(' ', '').replace('　', '')
    ans = ans.replace('√', '√').replace('×', '×')
    ans = ans.lower()
    return ans

if __name__ == '__main__':
    files_dir = os.path.join(os.path.dirname(__file__), 'files')
    all_questions = parse_questions_from_docx(files_dir)
    judge_questions = []
    single_choice_questions = []
    multi_choice_questions = []
    for q in all_questions:
        if q['qtype'] == 'judge':
            q['options'] = [{'label': 'A', 'text': '正确'}, {'label': 'B', 'text': '错误'}]
            judge_questions.append(q)
        elif q['qtype'] == 'single':
            single_choice_questions.append(q)
        elif q['qtype'] == 'multi':
            multi_choice_questions.append(q)
    save_to_db(judge_questions, single_choice_questions, multi_choice_questions)
    print(f'已保存 {len(judge_questions)} 道判断题，{len(single_choice_questions)} 道单选题，{len(multi_choice_questions)} 道多选题到 questions.db')
