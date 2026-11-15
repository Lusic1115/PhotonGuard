"""Validate, install or withdraw local knowledge packs without editing the solver."""

import argparse
import json
from pathlib import Path

from ..workflow import atomic_json
from .knowledge_catalog import Catalog, IDENTIFIER, builtin_topics, fingerprint, local_packs, merge_packs, validate_pack


def update(root, *, incoming=None, remove=None):
    folder=Path(root)/'knowledge-packs'
    if folder.is_symlink():raise ValueError('知识包目录不能是链接。')
    pack=validate_pack(json.loads(Path(incoming).read_bytes())) if incoming else None
    ident=pack['id'] if pack else remove
    if not IDENTIFIER.fullmatch(ident) or ident=='rc15-cards':
        raise ValueError('不能替换或撤回历史知识卡。')
    target=folder/(ident+'.json')
    if target.is_symlink() or not pack and not target.is_file():raise ValueError('未安装该专题包。')
    if pack and target.is_file():
        current=json.loads(target.read_bytes())
        if current['version']==pack['version'] and current!=pack:
            raise ValueError('此版本已经安装。修改条目后请使用新的知识包版本。')
    installed=[json.loads(path.read_bytes()) for path in local_packs(root) if path!=target]
    compiled=Catalog(merge_packs(installed+([pack] if pack else [])), builtin_topics())
    try:
        proof={'published':len(compiled.entries),'study_topics':len(compiled.study_topics),
               'catalog_version':compiled.version,'pack':ident}
        if pack:
            folder.mkdir(parents=True,exist_ok=True)
            atomic_json(target,pack)
            proof['file_fingerprint']=fingerprint(target.read_bytes())
        else:
            target.unlink()
        return proof
    finally:
        compiled.close()


def main(*, default_workspace=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--validate',type=Path)
    group.add_argument('--install',type=Path)
    group.add_argument('--remove')
    parser.add_argument('--workspace',type=Path,default=default_workspace)
    args=parser.parse_args()
    if args.validate:
        pack=validate_pack(json.loads(args.validate.read_bytes()))
        result={'id':pack['id'],'version':pack['version'],'fingerprint':fingerprint(args.validate.read_bytes())}
    else:
        if args.workspace is None:parser.error('安装或撤回需要 --workspace。')
        result=update(args.workspace,incoming=args.install,remove=args.remove)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
