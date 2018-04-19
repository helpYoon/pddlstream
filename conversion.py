from collections import namedtuple

from fast_downward import parse_lisp, parse_domain, run_fast_downward
from problem import Object

CONSTANTS = ':constants'
OBJECTS = ':objects'


def constant(name):
    return '{{{}}}'.format(name)
    #return '{{{}}}'.format(name)


class Atom(object):
    pass


def partition(array, i):
    return array[:i], array[i:]


def convert_head(atom):
    name, args = atom[0], atom[1:]
    return tuple([name] + map(Object.from_value, args))


convert_atom = convert_head


def is_head(expression):
    prefix = expression[0]
    return prefix not in OPERATORS


def convert_expression(expression):
    prefix = expression[0]
    if prefix in CONNECTIVES:
        children = expression[1:]
        return tuple([prefix] + map(convert_expression, children))
    elif prefix in QUANTIFIERS:
        assert(len(expression) == 3)
        parameters = expression[1]
        child = expression[2]
        return prefix, parameters, convert_expression(child)
    return convert_atom(expression)


def pddl_from_object(obj):
    return obj.pddl


def pddl_from_expression(tree):
    if isinstance(tree, Object):
        return pddl_from_object(tree)
    if isinstance(tree, str):
        return tree
    return '({})'.format(' '.join(map(pddl_from_expression, tree)))


def pddl_from_evaluation(evaluation):
    head = (evaluation.head.function,) + tuple(evaluation.head.args)
    if evaluation.value is True:
        return pddl_from_expression(head)
    if evaluation.value is False:
        return None
    expression = (EQ, head, str(evaluation.value))
    return pddl_from_expression(expression)


def pddl_from_evaluations(evaluations):
    return '\n\t\t'.join(sorted(filter(lambda s: s is not None,
                                       map(pddl_from_evaluation, evaluations))))


def pddl_from_objects(objects):
    return ' '.join(sorted(map(pddl_from_object, objects)))


def get_prefix(expression):
    return expression[0]


EQ = '=' # nxor
AND = 'and'
OR = 'or'
NOT = 'not'
EXISTS = 'exists'
FORALL = 'forall'
TotalCost = 'total-cost'
CONNECTIVES = (EQ, AND, OR, NOT)
QUANTIFIERS = (FORALL, EXISTS)
OPERATORS = CONNECTIVES + QUANTIFIERS

TOTAL_COST = 'total-cost'
DOMAIN_NAME = 'pddlstream'
PROBLEM_NAME = DOMAIN_NAME


Problem = namedtuple('Problem', ['init', 'goal', 'domain', 'streams', 'constants'])
Head = namedtuple('Head', ['function', 'args'])
Evaluation = namedtuple('Evaluation', ['head', 'value'])


def evaluations_from_init(init):
    evaluations = []
    for fact in init:
        prefix = get_prefix(fact)
        if prefix == EQ:
            head, value = fact[1:]
        elif prefix == NOT:
            head = fact[1]
            value = False
        else:
            head = fact
            value = True
        func, args = head[0], map(Object.from_value, head[1:])
        head = Head(func, args)
        evaluations.append(Evaluation(head, value))
    return evaluations


def objects_from_evaluations(evaluations):
    # TODO: assumes object predicates
    objects = set()
    for evaluation in evaluations:
        objects.update(evaluation.head.args)
    return objects

def get_pddl_problem(init_evaluations, goal_expression,
                     problem_name=DOMAIN_NAME, domain_name=PROBLEM_NAME, objective=(TOTAL_COST,)):
    # TODO: mako or some more elegant way of creating this
    objects = objects_from_evaluations(init_evaluations)
    s = '(define (problem {})\n' \
           '\t(:domain {})\n' \
           '\t(:objects {})\n' \
           '\t(:init {})\n' \
           '\t(:goal {})'.format(problem_name, domain_name,
                                 pddl_from_objects(objects),
                                 pddl_from_evaluations(init_evaluations),
                                 pddl_from_expression(goal_expression))
    if objective is not None:
        s += '\n\t(:metric minimize {})'.format(
            pddl_from_expression(objective))
    return s + ')\n'


def obj_from_pddl_plan(pddl_plan):
    if pddl_plan is None:
        return None
    return [(action, map(Object.from_name, args)) for action, args in pddl_plan]


def value_from_obj_plan(obj_plan):
    if obj_plan is None:
        return None
    return [(action, [a.value for a in args]) for action, args in obj_plan]


STREAM_ATTRIBUTES = [':stream', ':inputs', ':domain', ':outputs', ':certified']
Stream = namedtuple('Stream', ['name', 'inputs', 'gen_fn', 'domain', 'outputs'])


def parse_stream(stream_pddl, streams):
    stream_iter = iter(parse_lisp(stream_pddl))
    assert('define' == next(stream_iter))
    pddl_type, stream_name = next(stream_iter)
    assert('stream' == pddl_type)

    for stream in stream_iter:
        attributes = [stream[i] for i in range(0, len(stream), 2)]
        assert(STREAM_ATTRIBUTES == attributes)
        name, inputs, domain, outputs, certified = [stream[i] for i in range(1, len(stream), 2)]
        if name not in streams:
            raise ValueError('Undefined stream conditional generator: {}'.format(name))
        yield Stream(name, inputs, streams[name], outputs, certified)


def solve_finite(problem, **kwargs):
    init, goal, domain_pddl, stream_pddl, streams, constants = problem
    evaluations = evaluations_from_init(init)
    goal_expression = convert_expression(goal)
    domain = parse_domain(domain_pddl)
    problem_pddl = get_pddl_problem(evaluations, goal_expression,
                                    domain_name=domain.name)
    print(list(parse_stream(stream_pddl, streams)))

    return value_from_obj_plan(obj_from_pddl_plan(
        run_fast_downward(domain_pddl, problem_pddl)))


# Basic functions for parsing PDDL (Lisp) files.

# @ # $ % [] {} <> || \/
# What if we have #p1 and #p11

# https://docs.python.org/3.4/library/string.html
# mako/templating?

#class Not(object):

# TODO: start by requiring that all objects have a substituion


#def pddl_from_head(head):
#    return pddl_from_expression((head.name,) + head.args)

#def And(*expressions):
#    return (AND,) + expressions

# TODO: I think we do want everything to be an object at the end of the day
