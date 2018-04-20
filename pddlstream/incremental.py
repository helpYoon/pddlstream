import time

from pddlstream.conversion import value_from_obj_plan, \
    obj_from_pddl_plan, init_from_evaluations, evaluations_from_init, convert_expression, values_from_objects, \
    evaluation_from_fact
from pddlstream.fast_downward import solve_from_task, parse_domain, get_problem, \
    task_from_domain_problem
from pddlstream.instantiation import Instantiator
from pddlstream.stream import parse_stream, StreamResult, StreamInstance
from pddlstream.utils import INF, elapsed_time


def parse_problem(problem):
    init, goal, domain_pddl, stream_pddl, stream_map, constant_map = problem
    evaluations = set(evaluations_from_init(init))
    goal_expression = convert_expression(goal)
    domain = parse_domain(domain_pddl) # TODO: store PDDL here
    assert(len(domain.types) == 1)
    assert(not domain.constants)
    streams = parse_stream(stream_pddl, stream_map)
    return evaluations, goal_expression, domain, streams

#def solve_finite(evaluations, goal_expression, domain, domain_pddl, **kwargs):
#    problem_pddl = get_pddl_problem(evaluations, goal_expression, domain_name=domain.name)
#    plan_pddl, cost = solve_from_pddl(domain_pddl, problem_pddl, **kwargs)
#    return obj_from_pddl_plan(plan_pddl), cost

def solve_finite(evaluations, goal_expression, domain, **kwargs):
    problem = get_problem(evaluations, goal_expression, domain)
    task = task_from_domain_problem(domain, problem)
    plan_pddl, cost = solve_from_task(task, **kwargs)
    return obj_from_pddl_plan(plan_pddl), cost

def revert_solution(plan, cost, evaluations):
    return value_from_obj_plan(plan), cost, init_from_evaluations(evaluations)

def str_from_tuple(tup):
    return '({})'.format(', '.join(map(str, tup)))

def print_output_values_list(stream_instance, output_values_list):
    print('{}:{}->[{}]'.format(stream_instance.stream.name,
                               str_from_tuple(values_from_objects(stream_instance.input_values)),
                               ', '.join(map(str_from_tuple, map(values_from_objects, output_values_list)))))

def process_stream_queue(instantiator, evaluations, next_values_fn, revisit=True, verbose=True):
    stream_instance = instantiator.stream_queue.popleft()
    output_values_list = next_values_fn(stream_instance)
    if verbose:
        print_output_values_list(stream_instance, output_values_list)
    stream_results = []
    for output_values in output_values_list:
        stream_results.append(StreamResult(stream_instance, output_values))
        for fact in stream_results[-1].get_certified():
            evaluation = evaluation_from_fact(fact)
            instantiator.add_atom(evaluation)
            if evaluations is not None:
                evaluations.add(evaluation)
    if revisit and not stream_instance.enumerated:
        instantiator.stream_queue.append(stream_instance)
    return stream_results

def solve_current(problem, **kwargs):
    evaluations, goal_expression, domain, streams = parse_problem(problem)
    plan, cost = solve_finite(evaluations, goal_expression, domain, **kwargs)
    return revert_solution(plan, cost, evaluations)

def solve_exhaustive(problem, max_time=INF, verbose=True, **kwargs):
    start_time = time.time()
    evaluations, goal_expression, domain, streams = parse_problem(problem)
    instantiator = Instantiator(evaluations, streams)
    while instantiator.stream_queue and (elapsed_time(start_time) < max_time):
        process_stream_queue(instantiator, evaluations, StreamInstance.next_outputs, verbose=verbose)
    plan, cost = solve_finite(evaluations, goal_expression, domain, **kwargs)
    return revert_solution(plan, cost, evaluations)

def solve_incremental(problem, max_time=INF, max_cost=INF, verbose=True, **kwargs):
    start_time = time.time()
    num_iterations = 0
    best_plan = None; best_cost = INF
    evaluations, goal_expression, domain, streams = parse_problem(problem)
    instantiator = Instantiator(evaluations, streams)
    while elapsed_time(start_time) < max_time:
        num_iterations += 1
        print('Iteration: {} | Evaluations: {} | Cost: {} | Time: {:.3f}'.format(
            num_iterations, len(evaluations), best_cost, elapsed_time(start_time)))
        plan, cost = solve_finite(evaluations, goal_expression, domain, **kwargs)
        if cost < best_cost:
            best_plan = plan; best_cost = cost
        if (best_cost < max_cost) or not instantiator.stream_queue:
            break
        for _ in range(len(instantiator.stream_queue)):
            if max_time <= elapsed_time(start_time):
                break
            process_stream_queue(instantiator, evaluations, StreamInstance.next_outputs, verbose=verbose)
    return revert_solution(best_plan, best_cost, evaluations)
