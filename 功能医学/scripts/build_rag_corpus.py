import json, os, sys, traceback

def main():
    try:
        # Source knowledge statements (outside repo)
        source_path = r"C:\\RAG\\knowledge_statements.json"
        # Destination paths inside the cloned repo
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        reviewed_out = os.path.join(repo_root, 'backend', 'app', 'data', 'reviewed_statements.jsonl')
        reference_out = os.path.join(repo_root, 'backend', 'app', 'data', 'reference_only_raw.jsonl')
        count_path = os.path.join(repo_root, 'backend', 'app', 'data', 'knowledge_counts.json')
        os.makedirs(os.path.dirname(reviewed_out), exist_ok=True)
        os.makedirs(os.path.dirname(reference_out), exist_ok=True)
        os.makedirs(os.path.dirname(count_path), exist_ok=True)
        reviewed_cnt = 0
        reference_cnt = 0
        # Load whole JSON (may be large, but assumes fits in memory for this demo)
        with open(source_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with open(reviewed_out, 'w', encoding='utf-8') as rev_f, open(reference_out, 'w', encoding='utf-8') as ref_f:
            for obj in data:
                status = obj.get('review_status') or obj.get('reviewed')
                if status == 'reviewed':
                    rev_f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    reviewed_cnt += 1
                elif status == 'reference_only':
                    ref_f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    reference_cnt += 1
        counts = {'reviewed': reviewed_cnt, 'reference_only': reference_cnt, 'total': len(data)}
        with open(count_path, 'w', encoding='utf-8') as cnt_f:
            json.dump(counts, cnt_f, ensure_ascii=False, indent=2)
        print(f"Done. Reviewed: {reviewed_cnt}, Reference only: {reference_cnt}, Total: {len(data)}")
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
