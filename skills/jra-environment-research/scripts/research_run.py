"""Create a fresh research run; never scores, predicts or approves inputs."""
import argparse
import json
from pathlib import Path
from research_gate import ResearchSequence,ref


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--input',type=Path,action='append',required=True)
    args=parser.parse_args()
    run=ResearchSequence.create(args.root,args.run_dir,[ref(p) for p in args.input])
    print(json.dumps({'run_id':run.run_id,'directory':str(run.directory),'manifest':ref(run.directory/'manifest.json'),
                      'shared_initial':run.manifest['shared_initial'],'next_stage':'intake'}))


if __name__=='__main__':main()
