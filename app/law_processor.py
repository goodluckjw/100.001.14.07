import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import re
import os
import unicodedata
from collections import defaultdict

OC = os.getenv("OC", "chetera")
BASE = "http://www.law.go.kr"

def get_law_list_from_api(query):
    exact_query = f'"{query}"'
    encoded_query = quote(exact_query)
    page = 1
    laws = []
    while True:
        url = f"{BASE}/DRF/lawSearch.do?OC={OC}&target=law&type=XML&display=100&page={page}&search=2&knd=A0002&query={encoded_query}"
        res = requests.get(url, timeout=10)
        res.encoding = 'utf-8'
        if res.status_code != 200:
            break
        root = ET.fromstring(res.content)
        for law in root.findall("law"):
            laws.append({
                "법령명": law.findtext("법령명한글", "").strip(),
                "MST": law.findtext("법령일련번호", "")
            })
        if len(root.findall("law")) < 100:
            break
        page += 1
    return laws

def get_law_text_by_mst(mst):
    url = f"{BASE}/DRF/lawService.do?OC={OC}&target=law&MST={mst}&type=XML"
    try:
        res = requests.get(url, timeout=10)
        res.encoding = 'utf-8'
        return res.content if res.status_code == 200 else None
    except:
        return None

def clean(text):
    return re.sub(r"\s+", "", text or "")

def normalize_number(text):
    try:
        return str(int(unicodedata.numeric(text)))
    except:
        return text

def make_article_number(조문번호, 조문가지번호):
    return f"제{조문번호}조의{조문가지번호}" if 조문가지번호 and 조문가지번호 != "0" else f"제{조문번호}조"

def has_batchim(word):
    code = ord(word[-1]) - 0xAC00
    return (code % 28) != 0

def has_rieul_batchim(word):
    code = ord(word[-1]) - 0xAC00
    return (code % 28) == 8

def extract_chunk_and_josa(token, searchword):
    # 제외할 접미사 리스트 (만, 만을 등도 추가)
    suffix_exclude = ["의", "에", "에서", "으로서", "등", "에게", "만", "만을", "만이", "만은", "만을", "만으로"]
    
    # 가장 긴 접미사부터 확인
    sorted_suffixes = sorted(suffix_exclude, key=len, reverse=True)
    original_token = token
    
    for suffix in sorted_suffixes:
        if token.endswith(suffix) and len(token) > len(suffix):
            token = token[:-len(suffix)]
            break
            
    josa_list = ["으로", "이나", "과", "와", "을", "를", "이", "가", "나", "로", "은", "는"]
    pattern = re.compile(rf'({searchword}[가-힣0-9]*?)(?:{"|".join(josa_list)})?$')
    match = pattern.match(token)
    
    if match:
        chunk = match.group(1)
        josa = token[len(chunk):] if token[len(chunk):] in josa_list else None
        
        # 원래 토큰에서 접미사를 확인하고 청크에 추가
        for suffix in sorted_suffixes:
            if original_token.endswith(suffix) and not chunk.endswith(suffix):
                return chunk, josa, suffix
                
        return chunk, josa, None
    return token, None, None

def apply_josa_rule(orig, replaced, josa):
    b_has = has_batchim(replaced)
    # 일관성 있는 규칙 적용
    if josa is None:
        return f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.'
    
    rules = {
        "을": lambda: f'"{orig}"을 "{replaced}"로 한다.',
        "를": lambda: f'"{orig}"를 "{replaced}"로 한다.',
        "이": lambda: f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.',
        "가": lambda: f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.',
        "은": lambda: f'"{orig}"은 "{replaced}"로 한다.',
        "는": lambda: f'"{orig}"는 "{replaced}"로 한다.',
        "으로": lambda: f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.',
        "로": lambda: f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.'
    }
    return rules.get(josa, lambda: f'"{orig}"을 "{replaced}"로 한다.' if has_batchim(orig) else f'"{orig}"를 "{replaced}"로 한다.')()

def format_location(location):
    # 항번호가 비어있는 경우 "제항" 대신 "항" 제거
    location = re.sub(r'제(?=항)', '', location)
    
    # 호번호와 목번호 뒤의 period(.) 제거
    location = re.sub(r'(\d+)\.호', r'\1호', location)
    location = re.sub(r'([가-힣])\.목', r'\1목', location)
    
    return location

def group_locations(loc_list):
    # 각 위치 문자열에 대해 형식 수정 적용
    formatted_locs = [format_location(loc) for loc in loc_list]
    
    if len(formatted_locs) == 1:
        return formatted_locs[0]
    return 'ㆍ'.join(formatted_locs[:-1]) + ' 및 ' + formatted_locs[-1]

def run_amendment_logic(find_word, replace_word):
    amendment_results = []
    for idx, law in enumerate(get_law_list_from_api(find_word)):
        law_name = law["법령명"]
        mst = law["MST"]
        xml_data = get_law_text_by_mst(mst)
        if not xml_data:
            continue
        tree = ET.fromstring(xml_data)
        articles = tree.findall(".//조문단위")
        chunk_map = defaultdict(list)

        for article in articles:
            조번호 = article.findtext("조문번호", "").strip()
            조가지번호 = article.findtext("조문가지번호", "").strip()
            조문식별자 = make_article_number(조번호, 조가지번호)

            for 항 in article.findall("항"):
                항번호 = normalize_number(항.findtext("항번호", "").strip())
                항번호_부분 = f"제{항번호}항" if 항번호 else ""
                
                for 호 in 항.findall("호"):
                    호번호 = 호.findtext("호번호")
                    호내용 = 호.findtext("호내용", "") or ""
                    if find_word in clean(호내용):
                        tokens = re.findall(r'[가-힣A-Za-z0-9]+', 호내용)
                        for token in tokens:
                            if find_word in token:
                                chunk, josa, suffix = extract_chunk_and_josa(token, find_word)
                                replaced = chunk.replace(find_word, replace_word)
                                
                                # 위치 정보 저장 (항번호가 없는 경우 처리)
                                location = f"{조문식별자}{항번호_부분}제{호번호}호"
                                chunk_map[(chunk, replaced, josa, suffix)].append(location)

                    for 목 in 호.findall("목"):
                        목번호 = 목.findtext("목번호")
                        for m in 목.findall("목내용"):
                            if m.text:
                                줄들 = [line.strip() for line in m.text.splitlines() if line.strip()]
                                for 줄 in 줄들:
                                    if find_word in clean(줄):
                                        tokens = re.findall(r'[가-힣A-Za-z0-9]+', 줄)
                                        for token in tokens:
                                            if find_word in token:
                                                chunk, josa, suffix = extract_chunk_and_josa(token, find_word)
                                                replaced = chunk.replace(find_word, replace_word)
                                                
                                                # 위치 정보 저장 (항번호가 없는 경우 처리)
                                                location = f"{조문식별자}{항번호_부분}제{호번호}호{목번호}목"
                                                chunk_map[(chunk, replaced, josa, suffix)].append(location)

        if not chunk_map:
            continue

        result_lines = []
        for (chunk, replaced, josa, suffix), locations in chunk_map.items():
            loc_str = group_locations(sorted(set(locations)))
            
            # 접미사 처리
            if suffix:
                orig_with_suffix = chunk + suffix
                replaced_with_suffix = replaced + suffix
                rule = apply_josa_rule(orig_with_suffix, replaced_with_suffix, josa)
            else:
                rule = apply_josa_rule(chunk, replaced, josa)
                
            result_lines.append(f"{loc_str} 중 {rule}")

        prefix = chr(9312 + idx) if idx < 20 else f'({idx + 1})'
        amendment_results.append(f"{prefix} {law_name} 일부를 다음과 같이 개정한다.\n" + "\n".join(result_lines))

    return amendment_results if amendment_results else ["⚠️ 개정 대상 조문이 없습니다."]
