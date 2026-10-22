from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml
from scipy import sparse
from threadpoolctl import threadpool_limits

from ai_ecosystem.calibration.company_modeling_common import load_config, digest, file_hash
from ai_ecosystem.calibration.company_experiment_features import ExperimentText, NumericBlock, feature_blocks
from ai_ecosystem.calibration.company_experiment_metrics import choose, operating_points, summary, paired_bootstrap
from ai_ecosystem.calibration.run_company_model_experiments import fit_predict, verified_freeze, load_experiment_config, compare, evaluate_holdout, diagnostics

ROOT=Path(__file__).resolve().parents[1]


def cohort():
    return pd.DataFrame({'bvd_id':['a','b','c','d','e'],'label':['KEEP','UNKNOWN','EXCLUDE','KEEP','UNKNOWN'],
                         'state':['PROCESSABLE']*4+['NO_EVIDENCE'],'score':[4.,3.,2.,1.,np.nan],
                         'leakage_group_id':['a','b','c','d','e'],'normalized_domain':['a.at','b.at','c.at','d.at','e.at']})


class CompanyExperimentMetricsTest(unittest.TestCase):
    def setUp(self):
        self.config=load_experiment_config(ROOT/'config/company_model_experiments.yaml')

    def test_unknown_excluded_from_ap_not_budget(self):
        frame=cohort();values=summary(frame)
        self.assertAlmostEqual(values['average_precision'],5/6)
        rows=operating_points(frame,{**self.config,'fractions':[.4,.5,1.0],'absolute_k':[]})
        selected=next(r for r in rows if r['cohort']=='processable' and r['budget']==.5)
        self.assertEqual((selected['keep'],selected['unknown'],selected['exclude']),(1,1,0))
        self.assertEqual(selected['decidable_precision'],1)
        self.assertEqual(selected['known_keep_fraction'],.5)
        self.assertEqual(selected['keep_capture'],.5)
        all_rows=next(r for r in rows if r['cohort']=='operational' and r['budget']==1)
        self.assertEqual(all_rows['requested_count'],5);self.assertEqual(all_rows['selected_count'],4)

    def test_ties_and_na_metrics(self):
        f=cohort();f['score']=1.;f.loc[f.state.eq('NO_EVIDENCE'),'score']=np.nan
        rows=operating_points(f,{**self.config,'fractions':[.25],'absolute_k':[]})
        self.assertEqual(rows[-1]['keep'],1)
        self.assertIsNone(summary(f[f.label.eq('UNKNOWN')])['average_precision'])

    def test_paired_bootstrap_is_deterministic(self):
        config={**self.config,'bootstrap_replicates':10}
        a=paired_bootstrap({'x':cohort(),'y':cohort()},config,'x')
        self.assertEqual(a,paired_bootstrap({'x':cohort(),'y':cohort()},config,'x'))
        self.assertTrue(all(r['paired_delta_lower']==0 for r in a))

    def test_selection_tolerance_and_effort(self):
        rows=[{'candidate':'a','average_precision':.90,'selection_fraction_for_90_capture':.5,'complexity':1},
              {'candidate':'b','average_precision':.895,'selection_fraction_for_90_capture':.4,'complexity':1}]
        self.assertEqual(choose(rows,.01)['candidate'],'b')

    def test_supervised_fitting_excludes_unknown_and_missing(self):
        frame=cohort()
        cache={'ids':frame.bvd_id.tolist(),'blocks':{'L':sparse.csr_matrix([[1,0],[0,1],[0,1],[1,1],[0,0]])}}
        model,scores,benchmark=fit_predict(cache,'logistic_lexical',{'C':1.},'mean',frame,frame,self.config)
        self.assertEqual(benchmark['training_rows'],3)
        self.assertEqual(benchmark['training_unknown'],0)
        self.assertTrue(scores.loc[scores.bvd_id.eq('e'),'score'].isna().all())
        self.assertTrue(scores.loc[scores.bvd_id.eq('b'),'score'].notna().all())

    def test_holdout_closed_without_valid_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'Comparison must finish'):
                verified_freeze(Path(directory),self.config,{'canonical_build_hash':'x'})

    def test_diagnostics_do_not_inherit_baseline_candidate_name(self):
        frame=cohort().assign(candidate='winner',calibration_source='synthetic',current_pages_not_model_visible=0)
        numeric=pd.DataFrame({'bvd_id':frame.bvd_id,'availability_unique_contents':1,'relationship_shared':0,
                              'language_de_proportion':1.,'language_en_proportion':0.,'language_missing_proportion':0.}).set_index('bvd_id')
        _,examples=diagnostics({'baseline_random':frame},numeric)
        self.assertTrue(examples)
        self.assertEqual({r['candidate'] for r in examples},{'baseline_random'})


class CompanyExperimentPreprocessingTest(unittest.TestCase):
    def setUp(self):
        self.config=load_config(ROOT/'config/company_modeling_dataset.yaml')

    def test_nonempty_group_idf_and_no_validation_vocabulary(self):
        docs={'a':[('1','machine learning')],'b':[('2','machine systems')],'empty':[], 'v':[('3','validation secrets')]}
        p=ExperimentText(docs,{c:c for c in docs},self.config,prohibited_ids=['v'])
        p.fit(['a','b','empty'],[])
        self.assertEqual(p.vocabulary,{'machine':0})
        self.assertEqual(p.idf.tolist(),[1.])
        self.assertEqual(p.transform(['v'])['text_mean'].nnz,0)
        with self.assertRaises(ValueError):p.fit(['a'],[{'bvd_id':'v'}])

    def test_complete_groups_and_page_boundaries(self):
        docs={'a':[('1','machine'),('2','learning')],'b':[('3','machine'),('4','learning')]}
        p=ExperimentText(docs,{'a':'g','b':'g'},self.config)
        with self.assertRaises(ValueError):p.fit(['a'],[])
        p=ExperimentText(docs,{'a':'a','b':'b'},self.config).fit(['a','b'],[])
        self.assertNotIn('machine learning',p.vocabulary)

    def test_training_phrase_grounding_only(self):
        docs={'a':[('1','machine learning')],'b':[('2','machine learning')],'v':[('3','heldout discovery machine learning')]}
        supervision=[{'bvd_id':c,'eligible':True,'phrase':'machine learning','signal_type':'ai'} for c in ('a','b')]
        p=ExperimentText(docs,{c:c for c in docs},self.config).fit(['a','b'],supervision)
        self.assertIn(('ai','machine learning'),p.phrase_vocabulary)
        self.assertNotIn(('ai','heldout discovery'),p.phrase_vocabulary)

    def test_numeric_imputation_is_training_only(self):
        train=pd.DataFrame({'a':[1.,3.,np.nan],'b':[2.,2.,2.]})
        block=NumericBlock().fit(train,['a','b'])
        self.assertEqual(block.imputer.statistics_[0],2)
        before=block.imputer.statistics_.copy()
        block.transform(pd.DataFrame({'a':[1000.,np.nan],'b':[0.,5.]}))
        np.testing.assert_array_equal(before,block.imputer.statistics_)
        self.assertEqual(block.keep.tolist(),[True,False,True])

    def test_feature_blocks_are_explicit_disjoint(self):
        p=ROOT/'data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1/manifest.json'
        if not p.exists():self.skipTest('local frozen dataset unavailable')
        blocks=feature_blocks(json.loads(p.read_text())['predictive_allowlist'])
        self.assertEqual({k:len(v) for k,v in blocks.items()},{'N':27,'L':27,'Z':22})
        self.assertIn('lexical_explicit_ai_support',blocks['Z'])
        self.assertIn('lexical_explicit_ai_capped_support',blocks['L'])


class CompanyExperimentWorkflowTest(unittest.TestCase):
    def test_full_workflow_freeze_resume_and_single_holdout_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);data=root/'dataset';data.mkdir()
            config=load_experiment_config(ROOT/'config/company_model_experiments.yaml')
            config.update(dataset_directory=str(data),output_directory=str(root/'experiment'),
                          logistic_c=[1.0],tree_leaves=[3],tree_minimum_samples=[2],tree_iterations=5,
                          bootstrap_replicates=3,random_seeds=2)
            path=root/'experiment.yaml';path.write_text(yaml.safe_dump(config))
            source_config=load_config(ROOT/'config/company_modeling_dataset.yaml')
            (data/'config_snapshot.json').write_text(json.dumps(source_config))
            fields=[]
            for block in ('explicit_ai','adjacent','ambiguous'):
                fields.extend(['lexical_'+block+'_support','lexical_'+block+'_presence'])
            fields.extend(['availability_unique_contents','relationship_shared','structure_path_depth_mean',
                           'language_de_proportion','language_en_proportion','language_missing_proportion'])
            companies=[];splits=[];states=[];targets=[];holdout=[];sources=[];visibility=[]
            numeric=[];pages=[];contents=[];relationships=[];phrases=[]
            for i in range(36):
                cid=f'c{i:03}';domain=f'{cid}.at';label=('KEEP','EXCLUDE','UNKNOWN','UNKNOWN')[i%4]
                state='NO_EVIDENCE' if i%4==3 else 'PROCESSABLE'
                split='development' if i<20 else 'model_selection' if i<28 else 'internal_holdout'
                companies.append({'bvd_id':cid,'company_name':cid,'normalized_domain':domain})
                splits.append({'bvd_id':cid,'split':split,'leakage_group_id':cid,'development_fold':i//4 if i<20 else None})
                states.append({'bvd_id':cid,'state':state})
                target={'bvd_id':cid,'label':label,'website_target_eligible':state=='PROCESSABLE'}
                (holdout if split=='internal_holdout' else targets).append(target)
                sources.append({'bvd_id':cid,'calibration_source':'synthetic'})
                visibility.append({'bvd_id':cid,'current_pages_not_model_visible':0})
                values={key:float((i%4)+1) for key in fields}
                values.update(bvd_id=cid,lexical_explicit_ai_presence=float(label=='KEEP'),lexical_adjacent_presence=0.,relationship_shared=0.)
                numeric.append(values)
                if state=='PROCESSABLE':
                    text=('machine learning research' if label=='KEEP' else 'traditional trade services')+' '+cid
                    content_id=digest(text)
                    pages.append({'scraped_page_id':i,'title':'Research' if label=='KEEP' else 'Services','headings_text':'Company'})
                    contents.append({'content_id':content_id,'body_text':text})
                    relationships.append({'bvd_id':cid,'scraped_page_id':i,'content_id':content_id,'predictive_eligible':True})
                    phrases.append({'bvd_id':cid,'eligible':True,'phrase':'machine learning' if label=='KEEP' else 'trade services',
                                    'signal_type':'ai' if label=='KEEP' else 'conventional','normalized_domain':domain})
            tables={'companies':companies,'splits':splits,'operational_states':states,'targets':targets,
                    'evaluation_only/internal_holdout_targets':holdout,'audit/calibration_sources':sources,
                    'audit/visibility_gaps':visibility,'company_features':numeric,'pages':pages,'contents':contents,
                    'company_page_features':relationships,'supervision/phrase_candidates':phrases}
            manifest={'canonical_build_hash':'synthetic','predictive_allowlist':fields,
                      'holdout_targets':'evaluation_only/internal_holdout_targets.parquet','tables':{}}
            for name,rows in tables.items():
                location=data/(name+'.parquet');location.parent.mkdir(parents=True,exist_ok=True)
                pd.DataFrame(rows).to_parquet(location,index=False)
                manifest['tables'][name]={'file_hash':file_hash(location)}
            (data/'manifest.json').write_text(json.dumps(manifest))
            original=pd.read_parquet;opened=[]
            def guarded(location,*args,**kwargs):
                if 'internal_holdout_targets' in str(location):
                    self.assertTrue((root/'experiment/selection_freeze.json').exists())
                    self.assertTrue((root/'experiment/holdout/predictions_before_targets.parquet').exists())
                    opened.append(str(location))
                return original(location,*args,**kwargs)
            with threadpool_limits(limits=1),patch('pandas.read_parquet',side_effect=guarded):
                freeze=compare(path)
                self.assertEqual(opened,[])
                with self.assertRaises(FileExistsError):compare(path)
                self.assertEqual(compare(path,resume=True),freeze)
                result=evaluate_holdout(path)
                self.assertEqual(len(opened),1)
                self.assertEqual(result['processable_count'],6)
                with self.assertRaises(FileExistsError):evaluate_holdout(path)
                self.assertEqual(len(opened),1)
            self.assertTrue((root/'experiment/holdout/frozen_winner.joblib').exists())
            saved=json.loads((root/'experiment/selection_freeze.json').read_text())
            saved['payload']['pooling']='tampered'
            (root/'experiment/selection_freeze.json').write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'Invalid selection freeze'):
                verified_freeze(root/'experiment',config,manifest)


if __name__=='__main__':unittest.main()
