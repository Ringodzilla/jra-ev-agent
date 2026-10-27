#!/usr/bin/env python3
"""Role-dispatched research producers behind the research artifact chain."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'skills/jra-environment-research/scripts'))
from research_gate import ResearchSequence,Blocked,ref,read


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--stage',required=True)
    parser.add_argument('--role',required=True)
    parser.add_argument('--payload',type=Path)
    parser.add_argument('--produce',action='store_true')
    parser.add_argument('--dispatch',type=Path)
    parser.add_argument('--evidence',type=Path,action='append',default=[])
    args=parser.parse_args()
    run=ResearchSequence(args.run_dir)
    ready=False
    try:
        records,prior=run.require_next(args.stage)
        ready=True
        contract=run.manifest['branch']['steps'][len(records)]
        if args.role!=contract['role']:raise Blocked('Wrong dispatched role')
        refs=[ref(p) for p in args.evidence]
        if args.produce:
            if not args.dispatch or args.payload:raise Blocked('Producer needs dispatch and no supplied payload')
            dispatch_ref=ref(args.dispatch);dispatch=read(dispatch_ref)
            if dispatch.get('run_id')!=run.run_id or dispatch.get('stage')!=args.stage or dispatch.get('role')!=args.role or dispatch.get('record_type')!='orchestrator_role_dispatch':
                raise Blocked('Dispatch scope mismatch')
            refs.append(dispatch_ref)
            from research_numeric import produce
            payload=produce(args.stage,prior,run.root)
            payload.update(run_id=run.run_id,stage=args.stage,role=args.role,dispatch=dispatch_ref)
        else:
            if not args.payload:raise Blocked('Nonproducer stage requires role-authored payload')
            refs.append(ref(args.payload));payload=read(ref(args.payload))
        record=run.record(args.stage,payload,evidence=refs,checks={'role_dispatch_matches':True})
        print(json.dumps({'status':record['status'],'run_id':run.run_id,'stage':args.stage,'role':args.role}))
    except (Blocked,KeyError,ValueError,OSError,TypeError) as exc:
        if ready:
            try:
                run.require_next(args.stage)
                run.record(args.stage,{'run_id':run.run_id,'stage':args.stage,'role':args.role,'producer_failure':str(exc)},
                           evidence=[ref(args.dispatch)] if args.dispatch and args.dispatch.exists() else [ref(run.directory/'manifest.json')],
                           checks={'producer_completed':False})
            except (Blocked,KeyError,ValueError,OSError,TypeError):
                pass
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));raise SystemExit(2)


if __name__=='__main__':main()
