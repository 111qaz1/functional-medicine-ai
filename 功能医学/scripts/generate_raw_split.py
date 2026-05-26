import json, os, sys, traceback

def main():
    try:
        source_path = r"C:\\RAG\\knowledge_statements.json"
        repo_root = r"C:\\Users\\21547\\Desktop\\功能医学"
        out_dir = os.path.join(repo_root, 'backend', 'app', 'data')
        os.makedirs(out_dir, exist_ok=True)
        reviewed_path = os.path.join(out_dir, 'reviewed_statements.jsonl')
        reference_path = os.path.join(out_dir, 'reference_only_raw.jsonl')
        count_path = os.path.join(out_dir, 'knowledge_counts.json')
        reviewed_cnt = reference_cnt = 0
        with open(source_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with open(reviewed_path, 'w', encoding='utf-8') as rev_f, \
             open(reference_path, 'w', encoding='utf-8') as ref_f:
            for obj in data:
                status = obj.get('review_status') or obj.get('reviewed')
                if status == 'reviewed':
                    rev_f.write(json.dumps(obj, ensure_ascii=False) + '\n')
                    reviewed_cnt += 1
                elif status == 'reference_only':
                    ref_f.write(json.dumps(obj, ensure_ascii=False) + '\n')
                    reference_cnt += 1
        counts = {'reviewed': reviewed_cnt, 'reference_only': reference_cnt, 'total': len(data)}
        with open(count_path, 'w', encoding='utf-8') as cnt_f:
            json.dump(counts, cnt_f, ensure_ascii=False, indent=2)
        print(f"Generated: reviewed={reviewed_cnt}, reference_only={reference_cnt}, total={len(data)}")
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
