#!/usr/bin/env python3
"""Only supported entrypoint for this research branch; currently no numerical producer enabled."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'skills/jra-environment-research/scripts'))
from research_gate import ResearchSequence,Blocked,ref


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--stage',choices=('intake','collection','integrity_audit','approval_gate','analysis','simulation','probability','fit','review','report'),required=True)
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,action='append',default=[])
    args=parser.parse_args()
    run=ResearchSequence(args.run_dir)
    # Never invoke old score/baseline/fit CLIs or production/legacy EV functions.
    # record() independently derives stage checks; numerical stages are BLOCKED
    # until independently recomputing validators and producer dispatch exist.
    payload=json.loads(args.payload.read_text())
    try:
        result=run.record(args.stage,payload,evidence=[ref(p) for p in [args.payload,*args.evidence]],
                          checks={'payload_file_hash_verified':True})
    except (Blocked,KeyError,ValueError,OSError,TypeError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'numerical_calculations_executed':False}))
        raise SystemExit(2)
    print(json.dumps({'status':result['status'],'stage':args.stage,'numerical_calculations_executed':False}))


if __name__=='__main__':main()
