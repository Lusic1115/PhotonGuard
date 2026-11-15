"""HTTP entry points for learning, separate from the existing run action."""

from . import bounded_goal, bounded_intake, learning_followup, learning_lessons, slit_goal, tutor


def post(store, path, data):
    if path == '/api/experiments/learning/bounded/inspect':
        return bounded_intake.inspect(store, data)
    if path == '/api/experiments/learning/bounded/preview':
        return bounded_goal.preview(store, data)
    if path == '/api/experiments/learning/bounded/plan':
        return bounded_goal.prepare(store, data)
    if path == '/api/experiments/learning/lesson/start':
        return learning_lessons.start(store, data)
    if path == '/api/experiments/learning/lesson/respond':
        return learning_lessons.respond(store, data)
    if path == '/api/experiments/learning/slit/inspect':
        return slit_goal.inspect_goal(store, data)
    if path == '/api/experiments/learning/slit/preview':
        return slit_goal.prepare_goal(data)
    if path == '/api/experiments/learning/slit/plan':
        with store.lock:
            body=dict(data)
            binding=body.pop('study',None)
            if binding is None:
                return slit_goal.save_goal(store,body)
            from .knowledge_experiment import slit_form
            trace,_=slit_form(store,binding)
            return slit_goal.save_goal(store,body,study_origin=trace)
    if path == '/api/experiments/learning/ask':
        return tutor.answer(store, data)
    if path == '/api/experiments/learning/followup/preview':
        return learning_followup.preview_controls(store, data)
    if path == '/api/experiments/learning/followup/compensate':
        return learning_followup.preview_compensation(store, data)
    if path == '/api/experiments/learning/comparison':
        return learning_followup.comparison_summary(store, data)
    if path == '/api/experiments/learning/followup':
        return learning_followup.prepare(store, data)
    return None
