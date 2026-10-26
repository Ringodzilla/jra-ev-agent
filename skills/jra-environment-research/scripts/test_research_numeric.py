"""All numerical stages verified using entirely artificial measured fixtures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from research_gate import ref,sha,Blocked
from research_numeric import produce,check,verify_candidate,shared


class NumericTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.base=Path(self.tmp.name);self.prior={};selected=[];sources=[];labels=[];tracks={}
        def save(name,data):
            p=self.base/name;p.write_text(json.dumps(data,ensure_ascii=False));return ref(p)
        for index,(track,weather,condition,cushion) in enumerate([('東京','晴','良',9.5),('京都','雨','稍重',9.9),('新潟','曇','重',10.1)]):
            rid=f'synthetic{index}';rows=[];entries=[]
            for horse in (1,2):
                target=dict(race_id=rid,horse_id=f'h{horse}',horse_name=f'人工{horse}',horse_number=str(horse),frame_number=str(horse),target_track=track,target_surface='芝',target_distance='1600',target_track_condition=condition,target_weather=weather,target_race_date='2026-10-24',assigned_weight='55',current_jockey='人工騎手')
                entries.append(target)
                for run in (1,2):
                    rows.append(dict(target,run_index=str(run),date=f'2026-09-{20-run:02d}',position=str(horse+run-1),time=str(94+horse),distance='1600',last_3f=str(33+horse),passing_order=f'{horse}-{horse+run}',weight='55',course=track,history_surface='芝',track_condition=condition,jockey='人工騎手',popularity=str(horse),odds=str(2+horse)))
            nref=save(f'n{index}.json',{'rows':rows,'entries':entries})
            raw=self.base/f'raw{index}.html';raw.write_text('<table><tr><td class="place">1</td><td class="num">1</td></tr></table><dl><dt>単勝</dt><dd><div class="line"><span class="num">1</span><span class="yen">300円</span></div></dd></dl>')
            url=f'https://jra.jp/synthetic/{index}'
            source_ref=save(f'source{index}.json',{'result_url':url});sources.append(dict(source_ref,official_url=url))
            labels.append({'race_id':rid,'式別':'単勝','馬番':'1','払戻金':'300'})
            c={'measured_at':'2026-10-24T06:00:00+09:00','cushion':cushion}
            m={'measured_at':'2026-10-24T05:00:00+09:00','turf_moisture_goal':10+index,'turf_moisture_corner4':11+index}
            tracks[track]={'cushion_history':[c],'moisture_history':[m]}
            audit={'race_id':rid,'metadata_source':{'path':str(raw),'sha256':sha(raw),'url':url,'encoding':'utf-8'},'post_time':'2026-10-24T12:00:00+09:00','environment':{'cushion':c,'moisture':m}}
            selected.append({'race_id':rid,'normalized':nref,'audit':save(f'a{index}.json',audit)})
        self.prior['intake']={'labels':save('labels.json',{'sources':sources,'rows':labels}),'environment':save('env.json',{'target_date':'2026-10-24','pre_race_capture':False,'sources':{},'tracks':tracks})}
        self.prior['intake']['shared_initial']=shared(self.base)
        self.prior['integrity_audit']={'selected':selected}
    def tearDown(self):self.tmp.cleanup()
    def stages(self):
        for stage in ('analysis','simulation','probability','fit','review','report'):
            payload=produce(stage,self.prior,self.base)
            checks=check(stage,payload,self.prior,self.base)
            self.assertTrue(all(checks.values()),(stage,checks))
            self.prior[stage]=payload
    def test_all_numerical_stages(self):self.stages()
    def test_wrong_score_and_dropped_horse(self):
        payload=produce('analysis',self.prior,self.base);payload['rows'][0]['ability_score']+=.1
        self.assertFalse(all(check('analysis',payload,self.prior,self.base).values()))
        payload=produce('analysis',self.prior,self.base);payload['rows'].pop()
        self.assertFalse(all(check('analysis',payload,self.prior,self.base).values()))
    def test_smuggled_environment_offset(self):
        for stage in ('analysis','simulation'):
            self.prior[stage]=produce(stage,self.prior,self.base)
        payload=produce('probability',self.prior,self.base);payload['rows'][0]['environment_score']=.1
        self.assertFalse(all(check('probability',payload,self.prior,self.base).values()))
    def test_changed_probability(self):
        for stage in ('analysis','simulation'):
            self.prior[stage]=produce(stage,self.prior,self.base)
        payload=produce('probability',self.prior,self.base);payload['rows'][0]['win_prob']=.99
        self.assertFalse(all(check('probability',payload,self.prior,self.base).values()))
    def test_wrong_fixed_model_and_metrics(self):
        self.stages();fit=self.prior['fit'];fit['model']['ridge']=1
        self.assertFalse(verify_candidate(fit['dataset'],fit['model']))
        fit['model']['ridge']=100;fit['metrics']['candidate']['log_loss']+=.1
        self.assertFalse(all(check('fit',fit,self.prior,self.base).values()))
    def test_forged_review_and_production_report(self):
        self.stages();review=self.prior['review'];review['independent_metrics']['candidate']['brier']+=.5
        self.assertFalse(all(check('review',review,self.prior,self.base).values()))
        report=self.prior['report'];report['production_eligible']=True
        self.assertFalse(all(check('report',report,self.prior,self.base).values()))


if __name__=='__main__':unittest.main()
