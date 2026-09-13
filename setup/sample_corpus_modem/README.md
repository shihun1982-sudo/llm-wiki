# 합성 모뎀 코퍼스 (샘플)

`python setup/make_sample_corpus_modem.py` 로 생성된 가상 데이터. 문서 계약(front matter, schemas/) 을 따르는
issues/ cls/ sw_design/ hw_design/ coding_rules/ weekly/ tc/ 와 계약 없는 misc/ 를 포함한다. `questions.json` 은 평가셋.

사용: config.json 의 corpus_dirs 에 이 폴더를 넣고 `build --full`, `eval --questions setup/sample_corpus_modem/questions.json`.
