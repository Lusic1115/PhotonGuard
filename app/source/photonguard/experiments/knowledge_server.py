"""Serve PhotonGuard study pages with Python's standard library on loopback."""
import argparse
from pathlib import Path
import webbrowser

from ..workflow import WorkflowError, WorkflowHandler, make_server
from . import knowledge_routes

ASSETS = {
    '/knowledge', '/assets/knowledge.css', '/assets/knowledge.js', '/assets/knowledge_figures.js',
    '/assets/katex.min.js', '/assets/study_text.js',
}
API = '/api/experiments/knowledge/'


class KnowledgeHandler(WorkflowHandler):
    knowledge_only = True

    def parts(self):
        path, parts = super().parts()
        if path == '/' and self.command in ('GET','HEAD'):
            return '/knowledge', ['knowledge']
        if not (path in ASSETS or path.startswith(API)):
            raise WorkflowError('独立知识库提供查阅和学习问答。',404)
        return path, parts

    def get_experiment_api(self, path, parts):
        return knowledge_routes.get(self,parts[3:])

    def post_experiment_api(self, path, parts, data):
        if path==API+'ask':
            return knowledge_routes.ask(self,data)
        if path==API+'ai/connect':
            self.json(self.store.ai_binding.connect(data))
            return True
        if path==API+'ai/disconnect' and not data:
            self.json(self.store.ai_binding.disconnect())
            return True
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,default=Path.home()/'.photonguard-knowledge')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args(argv)
    with make_server(args.workspace,port=args.port,allow_workers=False,handler=KnowledgeHandler) as server:
        url = f'http://127.0.0.1:{server.server_port}/knowledge'
        print(url,flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            return


if __name__=='__main__':
    main()
