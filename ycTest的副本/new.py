import sqlite3
import os

def merge_questions_tables():
    """
    将数据库中的三张表（单选题、多选题、判断题）合并成一个新表
    """
    # 数据库文件路径
    db_path = 'questions.db'
    
    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        print(f"错误：数据库文件 {db_path} 不存在")
        return
    
    try:
        # 连接数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 删除旧表（如果存在）
        cursor.execute("DROP TABLE IF EXISTS merged_questions")
        
        # 创建合并后的新表
        create_table_sql = """
        CREATE TABLE merged_questions (
            id INTEGER PRIMARY KEY,
            qid TEXT,
            stem TEXT,
            options TEXT,
            answer TEXT,
            question_type TEXT
        )
        """
        cursor.execute(create_table_sql)
        
        # 从单选题表插入数据
        print("正在合并单选题...")
        cursor.execute("""
            INSERT INTO merged_questions (id, qid, stem, options, answer, question_type)
            SELECT ROW_NUMBER() OVER (ORDER BY id) as id, qid, stem, options, answer, 'single_choice' 
            FROM single_choice_questions
        """)
        single_count = cursor.rowcount
        print(f"已合并 {single_count} 道单选题")
        
        # 从多选题表插入数据
        print("正在合并多选题...")
        cursor.execute("""
            INSERT INTO merged_questions (id, qid, stem, options, answer, question_type)
            SELECT ROW_NUMBER() OVER (ORDER BY id) + ?, qid, stem, options, answer, 'multi_choice' 
            FROM multi_choice_questions
        """, (single_count,))
        multi_count = cursor.rowcount
        print(f"已合并 {multi_count} 道多选题")
        
        # 从判断题表插入数据
        print("正在合并判断题...")
        cursor.execute("""
            INSERT INTO merged_questions (id, qid, stem, options, answer, question_type)
            SELECT ROW_NUMBER() OVER (ORDER BY id) + ?, qid, stem, options, answer, 'judge' 
            FROM judge_questions
        """, (single_count + multi_count,))
        judge_count = cursor.rowcount
        print(f"已合并 {judge_count} 道判断题")
        
        # 提交事务
        conn.commit()
        
        # 验证合并结果
        cursor.execute("SELECT COUNT(*) FROM merged_questions")
        total_count = cursor.fetchone()[0]
        
        print(f"\n合并完成！")
        print(f"总计合并了 {total_count} 道题目：")
        print(f"  - 单选题：{single_count} 道")
        print(f"  - 多选题：{multi_count} 道")
        print(f"  - 判断题：{judge_count} 道")
        
        # 显示新表的结构
        print(f"\n新表 'merged_questions' 的结构：")
        cursor.execute("PRAGMA table_info(merged_questions)")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
        # 显示前几条数据作为示例
        print(f"\n前5条数据示例：")
        cursor.execute("SELECT id, qid, question_type, substr(stem, 1, 50) as stem_preview FROM merged_questions LIMIT 5")
        rows = cursor.fetchall()
        for row in rows:
            print(f"  ID: {row[0]}, QID: {row[1]}, 类型: {row[2]}, 题干: {row[3]}...")
        
        conn.close()
        print(f"\n数据库操作完成！")
        
    except sqlite3.Error as e:
        print(f"数据库错误：{e}")
    except Exception as e:
        print(f"程序错误：{e}")

def show_table_info():
    """
    显示各表的基本信息
    """
    try:
        conn = sqlite3.connect('questions.db')
        cursor = conn.cursor()
        
        tables = ['single_choice_questions', 'multi_choice_questions', 'judge_questions']
        
        print("各表信息：")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} 条记录")
        
        conn.close()
        
    except sqlite3.Error as e:
        print(f"数据库错误：{e}")

if __name__ == "__main__":
    print("=== 题目表合并程序 ===")
    print()
    
    # 显示原表信息
    show_table_info()
    print()
    
    # 执行合并
    merge_questions_tables()
