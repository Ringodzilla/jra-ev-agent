const fs = require('fs');
const path = require('path');

const raceId = '20260701_大井_06';
const capturedAt = '2026-06-30T07:02:21Z';
const targetUrl = 'https://www.keiba.go.jp/KeibaWeb_IPAT/TodayRaceInfo/DebaTable_ipat?k_raceDate=2026%2F07%2F01&k_raceNo=6&k_babaCode=20';

// position|date|condition|course|surface|distance|body|jockey|weight|time|passing|last3f|popularity|odds|weather|pace|race name|source URL|odds source URL
const horses = [
  {n:1,f:1,id:'30066402696',name:'リアルサファリ',j:'杉山海',w:51,h:[
    '9|2026-06-12|稍重|大井|ダート|1600|476|杉山海|51|1:43.4|10-10-10-10|39.5|9|61.2|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '7|2026-05-21|稍重|大井|ダート|1600|472|杉山海|51|1:44.8|8-10-10-10|39.6|10|67.4|雨|39.2|Ｃ１二 三 四|20,2026/05/21,5',
    '8|2026-04-30|稍重|大井|ダート|1600|473|杉山海|51|1:44.0|11-11-12-11|38.2|11|37.4|曇|39.4|Ｃ１三 四 五|20,2026/04/30,7',
    '6|2026-04-16|不良|大井|ダート|1600|473|吉井章|54|1:45.1|10-10-8-8|40.3|9|97.6|晴|38.9|Ｃ１三 四 五|20,2026/04/16,7',
    '3|2026-03-27|重|大井|ダート|1600|472|吉井章|54|1:44.1|11-11-9-8|38.9|8|66.9|晴|39.5|春灯特別Ｃ１三 四 五選抜|20,2026/03/27,10']},
  {n:2,f:2,id:'30068403586',name:'カズノトレジャー',j:'本田重',w:56,h:[
    '5|2026-06-12|稍重|大井|ダート|1600|463|本田重|56|1:42.6|10-11-11-11|38.3|8|54.0|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '4|2026-05-21|稍重|大井|ダート|1600|465|本田重|56|1:44.6|13-13-14-13|39.1|6|19.9|雨|39.2|Ｃ１二 三 四|20,2026/05/21,5',
    '11|2026-04-30|稍重|大井|ダート|1600|465|本田重|56|1:45.0|11-11-12-12|39.3|8|29.8|曇|39.4|Ｃ１三 四 五|20,2026/04/30,7',
    '3|2026-04-16|不良|大井|ダート|1600|469|本田重|56|1:44.2|6-6-5-4|39.7|6|49.0|晴|38.9|Ｃ１三 四 五|20,2026/04/16,7',
    '5|2026-03-13|良|大井|ダート|1600|472|藤本現|56|1:44.5|7-7-9-9|39.8|5|18.3|曇|37.9|Ｃ１三 四 五|20,2026/03/13,8']},
  {n:3,f:3,id:'30028400996',name:'クアッズ',j:'石川駿',w:56,h:[
    '3|2026-06-12|稍重|大井|ダート|1600|486|石川駿|56|1:42.0|1-1-1-1|39.6|3|5.7|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '1|2026-05-18|良|大井|ダート|1600|480|石川駿|56|1:42.2|1-1-1-1|39.1|6|15.7|晴|37.8|アフター・ディナー賞Ｃ１八 九|20,2026/05/18,8',
    '1|2026-05-01|不良|大井|ダート|1600|480|石川駿|56|1:42.7|1-1-1-1|40.4|5|15.1|晴||Ｃ２二 三 四|20,2026/05/01,6',
    '6|2026-04-17|重|大井|ダート|1400|481|石川駿|56|1:29.0|1-1-1|39.8|11|73.5|晴|36.9|バンブー賞Ｃ２四 五 六|20,2026/04/17,8',
    '7|2025-12-05|良|大井|ダート|1600|467|石川駿|56|1:44.8|5-6-7-6|41.4|8|55.6|晴|37.9|子どもの未来応援賞Ｃ２二 三 四|20,2025/12/05,8']},
  {n:4,f:4,id:'30032401796',name:'ツルマルヴィオレ',j:'御神訓',w:56,h:[
    '4|2026-06-12|稍重|大井|ダート|1600|493|御神訓|56|1:42.4|2-2-3-3|39.9|4|11.9|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '8|2026-05-21|稍重|大井|ダート|1600|492|御神訓|56|1:44.9|4-3-4-4|40.4|3|5.7|雨|39.2|Ｃ１二 三 四|20,2026/05/21,5',
    '8|2026-04-30|稍重|大井|ダート|1400|490|御神訓|56|1:29.9|4-6-5|39.7|3|12.0|曇|37.5|ツアリーヌ賞Ｃ１三 四 五|20,2026/04/30,8',
    '6|2025-09-18|良|大井|ダート|1400|497|御神訓|55|1:28.6|2-2-2|39.6|1||晴|36.7|Ｃ１二 三 四|20,2025/09/18,10',
    '2|2025-08-14|良|大井|ダート|1400|500|御神訓|55|1:28.0|4-2-1|38.8|1|1.2|晴||Ｃ１二 三 四|20,2025/08/14,9']},
  {n:5,f:5,id:'30003401296',name:'ハイアップグレード',j:'町田直',w:56,h:[
    '11|2026-06-12|稍重|大井|ダート|1600|501|安藤洋|56|1:43.9|12-12-11-11|39.7|13|222.0|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '10|2026-05-21|稍重|大井|ダート|1400|498|安藤洋|56|1:30.3|10-10-10|39.3|9|64.8|雨|37.3|Ｃ１二 三 四|20,2026/05/21,6',
    '14|2026-04-29|稍重|大井|ダート|1800|504|安藤洋|56|1:59.9|7-6-9-9|41.6|13|178.4|曇|39.0|ＳＴＡＲＴ！賞Ｃ１一選抜特別|20,2026/04/29,10',
    '12|2026-04-17|重|大井|ダート|2000|508|町田直|56|2:12.6|3-3-3-2|41.6|9|42.2|晴|38.0|北極星特別Ｃ１一選抜|20,2026/04/17,10',
    '2|2026-02-04|良|笠松|ダート|1600|498|丸野勝|57|1:46.5|7-7-4-3|39.5|4|15.5|晴||立春賞Ｃ２特選（イ）|23,2026/02/04,9']},
  {n:6,f:6,id:'30058403566',name:'サクラジェンヌ',j:'中山遥',w:53,h:[
    '12|2026-06-12|稍重|大井|ダート|1400|513|矢野貴|54|1:28.8|3-3-3|39.5|2|4.8|曇|36.7|てんびん座特別Ｃ１三 四 五選抜|20,2026/06/12,12',
    '2|2026-02-17|稍重|大井|ダート|1600|511|本田重|55|1:44.8|4-3-3-3|39.8|2|3.6|曇|39.1|河津桜特別Ｃ１Ｃ２選抜牝馬|20,2026/02/17,10',
    '3|2026-01-30|良|大井|ダート|1400|515|本田重|54|1:28.0|1-1-1|38.9|5|9.1|曇||Ｃ１三 四 五|20,2026/01/30,9',
    '3|2025-09-18|良|大井|ダート|1400|494|矢野貴|54|1:28.0|1-1-1|39.1|3|4.8|晴|36.7|Ｃ１二 三 四|20,2025/09/18,10',
    '7|2025-09-04|良|大井|ダート|1650|497|矢野貴|54|1:46.8|1-1-1-2|41.9|2|2.6|曇||オフトひたちなか賞Ｃ１一選抜特別|20,2025/09/04,12']},
  {n:7,f:7,id:'30048400196',name:'ハクアイソレイユ',j:'野畑凌',w:54,h:[
    '6|2026-06-12|稍重|大井|ダート|1600|460|吉井章|54|1:42.8|8-8-8-8|39.4|2|4.3|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '1|2026-05-19|良|大井|ダート|1600|456|野畑凌|54|1:42.5|11-11-9-9|38.3|3|5.3|晴|38.5|Ｃ１五 六 七|20,2026/05/19,6',
    '3|2026-04-29|稍重|大井|ダート|1600|455|野畑凌|54|1:43.5|8-8-6-6|39.0|4|7.2|曇|38.8|Ｃ１六 七 八|20,2026/04/29,6',
    '4|2026-04-14|良|大井|ダート|1600|449|野畑凌|54|1:43.9|10-10-10-7|38.4|5||晴||フラミンゴ・レディ賞Ｃ１八 九|20,2026/04/14,8',
    '2|2026-03-27|重|大井|ダート|1600|454|野畑凌|54|1:43.3|6-6-2-2|39.3|2|4.4|晴|39.6|Ｃ２四 五 六|20,2026/03/27,4']},
  {n:8,f:7,id:'30067403596',name:'ドバイミッション',j:'藤田凌',w:56,h:[
    '1|2026-06-09|不良|大井|ダート|1600|550|藤田凌|56|1:42.9|1-2-1-1|39.7|2|4.4|曇|37.9|Ｃ１八 九|20,2026/06/09,7',
    '6|2026-01-27|良|大井|ダート|2000|558|藤田凌|56|2:11.8|3-3-3-3|39.0|3||晴|38.5|小石川見附特別Ｃ１一選抜|20,2026/01/27,12',
    '1|2025-12-31|稍重|大井|ダート|1400|551|藤田凌|56|1:27.2|3-3-3|37.3|5|9.2|曇|37.1|Ｃ２四 五 六|20,2025/12/31,5',
    '2|2025-11-06|稍重|門別|ダート|1800|554|服部茂|57|1:59.8|5-5-5-4|40.1|1|1.5|曇||３歳以上 Ｃ３－２ Ｃ４－１|36,2025/11/06,8',
    '2|2025-10-23|稍重|門別|ダート|2000|544|服部茂|57|2:13.2|5-5-5-1|41.4|1|2.4|晴||静内産米「万馬券」特別Ｃ３－２|36,2025/10/23,11']},
  {n:9,f:8,id:'40022406076',name:'グレン',j:'安藤洋',w:56,h:[
    '10|2025-08-09|稍重|札幌|芝|2000|496|松本大|58|2:07.0|10-8-9-10|37.5|11|383.7|晴|38.3|藻岩山特別３歳上２勝クラス|JRA,2025/08/09,10',
    '12|2025-07-26|良|札幌|芝|2600|500|松本大|58|2:43.4|4-4-4-7|38.9|10|72.4|雨|35.9|ライラック賞３歳上２勝クラス|JRA,2025/07/26,10',
    '14|2025-01-18|良|中京|ダート|1800|498|團野大|58|1:55.4|6-5-6-5|37.6|11|69.1|晴|37.8|濃尾特別４歳上２勝クラス|JRA,2025/01/18,9',
    '10|2024-12-22|良|京都|ダート|1800|500|團野大|58|1:54.9|4-4-4-4|38.9|10|34.9|晴|37.2|クリスマスエルフ賞３歳上２勝クラス|JRA,2024/12/22,9',
    '16|2024-08-25|良|中京|ダート|1800|494|西塚洸|58|1:56.2|1-2-2-3|41.0|9|45.1|曇|36.6|大府特別３歳上２勝クラス|JRA,2024/08/25,9']},
  {n:10,f:8,id:'30064406986',name:'コーゲンシルバー',j:'中村尚',w:56,h:[
    '2|2026-06-12|稍重|大井|ダート|1600|464|中村尚|56|1:41.9|4-4-4-4|39.1|7|28.3|曇|37.7|ジン・ライム賞Ｃ１三 四 五|20,2026/06/12,8',
    '5|2026-05-21|稍重|大井|ダート|1600|469|石川駿|56|1:44.6|6-6-6-7|39.9|7|26.2|雨|39.2|Ｃ１二 三 四|20,2026/05/21,5',
    '5|2026-04-29|稍重|大井|ダート|1600|476|中村尚|56|1:44.1|4-4-4-4|39.8|7|28.5|曇|38.8|Ｃ１六 七 八|20,2026/04/29,6',
    '9|2026-04-16|不良|大井|ダート|1200|468|石川駿|56|1:15.5|8-13|39.4|13|119.7|晴|35.4|Ｃ１三 四 五|20,2026/04/16,9',
    '9|2026-03-27|重|大井|ダート|1200|476|石川駿|56|1:14.9|4-5|38.8|5|23.6|晴|35.6|八丈島フリージア賞Ｃ１三 四 五|20,2026/03/27,9']}
];

const timeSeconds = s => { const p=s.split(':').map(Number); return p.length===2 ? +(p[0]*60+p[1]).toFixed(1) : +s; };
const csv = v => { if (v === null || v === undefined) return ''; const s=String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s; };
const resultUrl = code => { const [b,d,r]=code.split(','); if(b==='JRA') return 'https://www.jra.go.jp/JRADB/accessS.html'; return `https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable?k_babaCode=${b}&k_raceDate=${encodeURIComponent(d)}&k_raceNo=${r}`; };
const rows=[];
for (const h of horses) h.h.forEach((line,i)=>{ const a=line.split('|'); rows.push({row_id:`row_20260701oi06_${String(h.n).padStart(2,'0')}_${i+1}`,race_id:raceId,horse_id:h.id,horse_name:h.name,frame_number:h.f,horse_number:h.n,current_jockey:h.j,assigned_weight:h.w,current_odds:'',current_popularity:'',target_track:'大井',target_race_date:'2026-07-01',target_race_number:6,target_surface:'ダート',target_distance:1600,target_weather:'',target_track_condition:'',target_conditions_captured_at:'',horse_country:'',run_index:i+1,position:a[0],date:a[1],track_condition:a[2],course:a[3],surface:a[4],distance:a[5],horse_body_weight:a[6],jockey:a[7],weight:a[8],time:timeSeconds(a[9]),passing_order:a[10],last_3f:a[11],popularity:a[12],odds:a[13],weather:a[14],pace:a[15],race_name:a[16],source_url:resultUrl(a[17]),odds_source:a[13]?'official/verified result':'unavailable',odds_source_url:a[13]?resultUrl(a[17]):''}); });

const common=['row_id','race_id','horse_id','horse_name','frame_number','horse_number','current_jockey','assigned_weight','current_odds','current_popularity','target_track','target_race_date','target_race_number','target_surface','target_distance','target_weather','target_track_condition','target_conditions_captured_at','horse_country','run_index','date','race_name','course','distance','position','time','weight','jockey','pace','last_3f','track_condition','weather','passing_order','odds','popularity'];
const detail=common.slice(1).concat(['surface','horse_body_weight','source_url','odds_source','odds_source_url']);
const entriesCols=['race_id','horse_id','horse_name','frame_number','horse_number','current_jockey','assigned_weight','current_odds','current_popularity','target_track','target_race_date','target_race_number','target_surface','target_distance','target_weather','target_track_condition','target_conditions_captured_at','horse_country','history_count'];
const toCsv=(cols,data)=>cols.join(',')+'\n'+data.map(x=>cols.map(k=>csv(x[k])).join(',')).join('\n')+'\n';
const entriesRows=horses.map(h=>({race_id:raceId,horse_id:h.id,horse_name:h.name,frame_number:h.f,horse_number:h.n,current_jockey:h.j,assigned_weight:h.w,current_odds:'',current_popularity:'',target_track:'大井',target_race_date:'2026-07-01',target_race_number:6,target_surface:'ダート',target_distance:1600,target_weather:'',target_track_condition:'',target_conditions_captured_at:'',horse_country:'',history_count:5}));

const outDir=path.join(__dirname,'..','data','processed');
fs.mkdirSync(outDir,{recursive:true});
const last5=toCsv(common,rows), hist=toCsv(detail,rows), entriesCsv=toCsv(entriesCols,entriesRows);
fs.writeFileSync(path.join(outDir,'nar_20260701_ooi_06_last5.csv'),last5);
fs.writeFileSync(path.join(outDir,'nar_20260701_ooi_06_history_detail.csv'),hist);
fs.writeFileSync(path.join(outDir,'nar_20260701_ooi_06_entries.csv'),entriesCsv);

const missing={}; for(const k of ['date','race_name','course','distance','position','time','weight','jockey','last_3f','track_condition','passing_order','popularity','horse_body_weight','weather','pace','odds']) missing[k]=rows.filter(r=>r[k]==='').length;
const dq={race_id:raceId,status:'PARTIAL_UNPUBLISHED',source_url:targetUrl,captured_at:capturedAt,entry_count:horses.length,history_requested:50,history_collected:rows.length,missing_history_fields:missing,current_unpublished_fields:{current_odds:10,current_popularity:10,current_body_weight:10,target_weather:1,target_track_condition:1,combo_odds:['複勝','ワイド','枠連','馬連','馬単','三連複','三連単']},unresolved_history:{odds:rows.filter(r=>r.odds==='').map(r=>({horse:r.horse_name,run_index:r.run_index,date:r.date,reason:'final odds not verifiable from available authoritative result'})),pace:rows.filter(r=>r.pace==='').map(r=>({horse:r.horse_name,run_index:r.run_index,date:r.date,reason:'sectional time not published by source'})),weather:rows.filter(r=>r.weather==='').map(r=>({horse:r.horse_name,run_index:r.run_index,date:r.date,reason:'weather not recoverable from available result'}))},checks:{entry_count_is_10:horses.length===10,history_count_is_50:rows.length===50,history_per_horse_is_5:horses.every(h=>h.h.length===5),row_ids_unique:new Set(rows.map(r=>r.row_id)).size===50,required_identity_fields_complete:rows.every(r=>r.horse_id&&r.horse_name&&r.date&&r.position&&r.time!==''),current_values_not_fabricated:rows.every(r=>r.current_odds===''&&r.current_popularity===''),recalculation_1:rows.length,recalculation_2:horses.reduce((n,h)=>n+h.h.length,0),recalculations_match:rows.length===horses.reduce((n,h)=>n+h.h.length,0)},notes:['2026-06-30 16:02 JST時点で対象レースの当日単勝・複勝・連系オッズ、馬体重、天候、馬場状態は未公表。','未公表の当日値は欠損補完せず空欄を維持。','過去走の結果・人気・馬体重・通過順等はNAR/JRA公式結果を基礎に照合。最終値を確認できない過去オッズと公式非公表ラップは空欄。']};
const reportDir=path.join(__dirname,'..','report','races','20260701_大井_06'); fs.mkdirSync(reportDir,{recursive:true}); fs.writeFileSync(path.join(reportDir,'data_quality.json'),JSON.stringify(dq,null,2)+'\n');
console.log(JSON.stringify({files:4,rows:rows.length,missing,status:dq.status}));
