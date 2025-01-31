#!/usr/bin/env python

from __future__ import print_function

try:
    import pybullet as p
except ImportError:
    raise ImportError('This example requires PyBullet (https://pypi.org/project/pybullet/)')

from pddlstream.algorithms.meta import solve, create_parser
from pddlstream.algorithms.search import ABSTRIPSLayer
from pddlstream.language.generator import from_gen_fn, from_list_fn, from_fn, from_test, accelerate_list_gen_fn
from pddlstream.utils import read, get_file_path, Profiler
from pddlstream.language.constants import PDDLProblem, And, Equal, print_solution
from pddlstream.language.stream import StreamInfo

from examples.pybullet.pr2_belief_llm.primitives import Scan, ScanRoom, Detect, Register, \
    plan_head_traj, get_cone_commands, move_look_trajectory, get_vis_base_gen, \
    get_inverse_visibility_fn, get_in_range_test, VIS_RANGE, REG_RANGE
from examples.pybullet.pr2_belief_llm.problems import get_problem1, USE_DRAKE_PR2, create_pr2
from examples.pybullet.utils.pybullet_tools.pr2_utils import ARM_NAMES, get_arm_joints, attach_viewcone, \
    is_drake_pr2, get_group_joints, get_group_conf
from examples.pybullet.utils.pybullet_tools.utils import set_pose, get_pose, connect, clone_world, \
    disconnect, set_client, add_data_path, WorldSaver, wait_for_user, get_joint_positions, get_configuration, \
    set_configuration, ClientSaver, HideOutput, is_center_stable, add_body_name, draw_base_limits
from examples.pybullet.utils.pybullet_tools.pr2_primitives import Conf, get_ik_ir_gen, get_motion_gen, get_stable_gen, \
    get_grasp_gen, Attach, Detach, apply_commands, Trajectory, get_base_limits
from examples.discrete_belief.run import revisit_mdp_cost, MAX_COST, clip_cost


def pddlstream_from_state(state, teleport=False):
    task = state.task
    robot = task.robot

    domain_pddl = read(get_file_path(__file__, 'domain.pddl'))
    stream_pddl = read(get_file_path(__file__, 'stream.pddl'))
    constant_map = {
        'base': 'base',
        'left': 'left',
        'right': 'right',
        'head': 'head',
    }

    base_conf = Conf(robot, get_group_joints(robot, 'base'), get_group_conf(robot, 'base'))
    scan_cost = 1
    init = [
        ('BConf', base_conf),
        ('AtBConf', base_conf),
        Equal(('MoveCost',), 1),
        Equal(('PickCost',), 1),
        Equal(('PlaceCost',), 1),
        Equal(('ScanCost',), scan_cost),
        Equal(('RegisterCost',), 1),
    ]
    holding_arms = set()
    holding_bodies = set()
    for attach in state.attachments.values():
        holding_arms.add(attach.arm)
        holding_bodies.add(attach.body)
        init += [('Grasp', attach.body, attach.grasp),
                 ('AtGrasp', attach.arm, attach.body, attach.grasp)]
    for arm in ARM_NAMES:
       joints = get_arm_joints(robot, arm)
       conf = Conf(robot, joints, get_joint_positions(robot, joints))
       init += [('Arm', arm), ('AConf', arm, conf), ('AtAConf', arm, conf)]
       if arm in task.arms:
           init += [('Controllable', arm)]
       if arm not in holding_arms:
           init += [('HandEmpty', arm)]
    for body in task.get_bodies():
        if body in holding_bodies:
            continue
        pose = state.poses[body]
        init += [('Pose', body, pose), ('AtPose', body, pose),
                 ('Observable', pose),
        ]

    init += [('Scannable', body) for body in task.rooms + task.surfaces]
    init += [('Registerable', body) for body in task.movable]
    init += [('Graspable', body) for body in task.movable]
    for body in task.get_bodies():
        supports = task.get_supports(body)
        if supports is None:
            continue
        for surface in supports:
            p_obs = state.b_on[body].prob(surface)
            cost = revisit_mdp_cost(0, scan_cost, p_obs)
            init += [('Stackable', body, surface),
                     Equal(('LocalizeCost', surface, body), clip_cost(cost))]
            if is_center_stable(body, surface):
                if body in holding_bodies:
                    continue
                pose = state.poses[body]
                init += [('Supported', body, pose, surface)]

    for body in task.get_bodies():
        if state.is_localized(body):
            init.append(('Localized', body))
        else:
            init.append(('Uncertain', body))
        if body in state.registered:
            init.append(('Registered', body))

    goal = And(*[('Holding', a, b) for a, b in task.goal_holding] + \
           [('On', b, s) for b, s in task.goal_on] + \
           [('Localized', b) for b in task.goal_localized] + \
           [('Registered', b) for b in task.goal_registered])

    stream_map = {
        'sample-pose': from_gen_fn(get_stable_gen(task)),
        'sample-grasp': from_list_fn(get_grasp_gen(task)),
        'inverse-kinematics': from_gen_fn(get_ik_ir_gen(task, teleport=teleport)),
        'plan-base-motion': from_fn(get_motion_gen(task, teleport=teleport)),

        'test-vis-base': from_test(get_in_range_test(task, VIS_RANGE)),
        'test-reg-base': from_test(get_in_range_test(task, REG_RANGE)),

        'sample-vis-base': accelerate_list_gen_fn(from_gen_fn(get_vis_base_gen(task, VIS_RANGE)), max_attempts=25),
        'sample-reg-base': accelerate_list_gen_fn(from_gen_fn(get_vis_base_gen(task, REG_RANGE)), max_attempts=25),
        'inverse-visibility': from_fn(get_inverse_visibility_fn(task)),
    }

    return PDDLProblem(domain_pddl, constant_map, stream_pddl, stream_map, init, goal)


#######################################################


def post_process(state, plan, replan_obs=True, replan_base=False, look_move=False):
    if plan is None:
        return None
    # TODO: refine actions
    robot = state.task.robot
    commands = []
    uncertain_base = False
    expecting_obs = False
    for i, (name, args) in enumerate(plan):
        if replan_obs and expecting_obs:
            break
        saved_world = WorldSaver()
        if name == 'move_base':
            c = args[-1]
            [t] = c.commands
            if look_move:
                new_commands = [move_look_trajectory(state.task, t)]
            else:
                new_commands = [t]
            if replan_base:
                uncertain_base = True
        elif name == 'pick':
            if uncertain_base:
                break
            a, b, p, g, _, c = args
            [t] = c.commands
            attach = Attach(robot, a, g, b)
            new_commands = [t, attach, t.reverse()]
        elif name == 'place':
            if uncertain_base:
                break
            a, b, p, g, _, c = args
            [t] = c.commands
            detach = Detach(robot, a, b)
            new_commands = [t, detach, t.reverse()]
        elif name == 'scan':
            o, p, bq, hq, ht = args
            ht0 = plan_head_traj(state.task, hq.values)
            new_commands = [ht0]
            if o in state.task.rooms:
                attach, detach = get_cone_commands(robot)
                new_commands += [attach, ht, ScanRoom(robot, o), detach]
            else:
                new_commands += [ht, Scan(robot, o)]
        elif name == 'localize':
            r, _, o, _ = args
            new_commands = [Detect(robot, r, o)]
            expecting_obs = True
        elif name == 'register':
            o, p, bq, hq, ht = args
            ht0 = plan_head_traj(state.task, hq.values)
            register = Register(robot, o)
            new_commands = [ht0, register]
            expecting_obs = True
        else:
            raise ValueError(name)
        saved_world.restore()
        for command in new_commands:
            if isinstance(command, Trajectory) and command.path:
                command.path[-1].assign()
        commands += new_commands
    return commands


#######################################################


def plan_commands(state, args, profile=True, verbose=True):
    sim_world = connect(use_gui=args.viewer)
    task = state.task
    robot_conf = get_configuration(task.robot)
    robot_pose = get_pose(task.robot)
    with ClientSaver(sim_world):
        with HideOutput():
            robot = create_pr2(use_drake=USE_DRAKE_PR2)
        set_pose(robot, robot_pose)
        set_configuration(robot, robot_conf)
    mapping = clone_world(client=sim_world, exclude=[task.robot])
    assert all(i1 == i2 for i1, i2 in mapping.items())
    set_client(sim_world)
    saver = WorldSaver()

    pddlstream_problem = pddlstream_from_state(state, teleport=args.teleport)
    _, _, _, stream_map, init, goal = pddlstream_problem
    print('Init:', sorted(init, key=lambda f: f[0]))
    if verbose:
        print('Goal:', goal)
        print('Streams:', stream_map.keys())

    stream_info = {
        'test-vis-base': StreamInfo(eager=True, p_success=0),
        'test-reg-base': StreamInfo(eager=True, p_success=0),
    }
    hierarchy = [
        ABSTRIPSLayer(pos_pre=['AtBConf']),
    ]

    with Profiler(field='cumtime', num=10 if profile else None):
        solution = solve(pddlstream_problem, algorithm=args.algorithm, unit_costs=args.unit,
                         stream_info=stream_info, hierarchy=hierarchy, debug=False,
                         success_cost=MAX_COST, verbose=verbose)
        plan, cost, evaluations = solution
        if MAX_COST <= cost:
            plan = None
        print_solution(solution)
        print('Finite cost:', cost < MAX_COST)
        commands = post_process(state, plan)
    saver.restore()
    disconnect()
    return commands


#######################################################

def main(time_step=0.01):
    parser = create_parser()
    parser.add_argument('-teleport', action='store_true', help='Teleports between configurations')
    parser.add_argument('-viewer', action='store_true', help='Enable the viewer while planning')
    args = parser.parse_args()
    print('Arguments:', args)

    real_world = connect(use_gui=not args.viewer)
    add_data_path()
    task, state = get_problem1(localized='rooms', p_other=.2) # surfaces | rooms
    for body in task.get_bodies():
        add_body_name(body)

    robot = task.robot
    assert(USE_DRAKE_PR2 == is_drake_pr2(robot))
    attach_viewcone(robot)
    draw_base_limits(get_base_limits(robot), color=(0, 1, 0))
    step = 0
    while True:
        step += 1
        print('\n' + 50 * '-')
        print(step, state)
        wait_for_user()
        with ClientSaver():
            commands = plan_commands(state, args)
        print()
        if commands is None:
            print('Failure!')
            break
        if not commands:
            print('Success!')
            break
        apply_commands(state, commands, time_step=time_step)

    print(state)
    wait_for_user()
    disconnect()


if __name__ == '__main__':
    main()
