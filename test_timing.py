from backend.utils.timing import PipelineTiming
import time

t = PipelineTiming()
class FakePlan:
    planner_elapsed = 0.5
    steps = [1]
    is_cross_domain = False
    has_dependencies = False
t.record_planner(FakePlan())

# === Test 1: Native function matched ===
t.start_step(1, 'feedback')
t.record_native_attempt(1, matched=True, function_name='csat_trend_last_n_months', match_elapsed=1.2)
time.sleep(0.01)
t.end_step(1, native_function='csat_trend_last_n_months', native_match_time=1.2, native_params={'n_months': 12})
t.start_format()
time.sleep(0.01)
t.end_format()
t.finish()
print('=== NATIVE MATCHED ===')
print(t.to_markdown())

# === Test 2: Native attempted but no match, fell through to LLM ===
t2 = PipelineTiming()
t2.record_planner(FakePlan())
t2.start_step(1, 'feedback')
t2.record_native_attempt(1, matched=False, match_elapsed=0.8)
time.sleep(0.01)
t2.end_step(1, executor_timings={'generate_dax': 3.5, 'execute_dax': 1.2})
t2.start_format()
time.sleep(0.01)
t2.end_format()
t2.finish()
print()
print('=== NATIVE NOT MATCHED (fallthrough) ===')
print(t2.to_markdown())
