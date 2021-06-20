# -*- coding: UTF-8 -*-
from typing import Iterable
from commonroad.common.file_reader import CommonRoadFileReader
from commonroad.visualization.draw_dispatch_cr import draw_object

import os
import numpy as np
import matplotlib.pyplot as plt
import copy

from conf_lanelet_checker import conf_lanelet_checker, potential_conf_lanelet_checkerv2
from detail_central_vertices import detail_cv

from commonroad.geometry.shape import Rectangle
from commonroad.scenario.obstacle import DynamicObstacle, ObstacleType
from commonroad.scenario.trajectory import Trajectory, State
from commonroad.prediction.prediction import TrajectoryPrediction
from vehiclemodels import parameters_vehicle3
from commonroad.visualization.mp_renderer import MPRenderer

'''
缺少要素：
    1. 如何系统的考虑前车的影响？目前场景没有前车
    2. 全局lanelet规划器。比如路口在行驶的过程中，如何知道此时应该前行还是右转。

'''



def distance_lanelet(center_line, s, p1, p2):
    ''' 计算沿着道路中心线的路程. p2 - p1（正数说明p2在道路后方）
         直线的时候，保证是直线距离；曲线的时候，近似正确
    Args: 
        center_line: 道路中心线；
        s : 道路中心线累积距离;
        p1, p2: 点1， 点2
    Return:

    '''
    # 规范化格式。必须是numpy数组。并且m*2维，m是点的数量
    if type(center_line) is not np.ndarray:
        center_line = np.array(center_line)
    if center_line.shape[1] !=2:
        center_line = center_line.T
    if center_line.shape[0] == 2:
        print('distance_lanelet warning! may wrong size of center line input. check the input style ')

    d1 = np.linalg.norm(center_line - p1, axis=1)
    i1 = np.argmin(d1)
    d2 = np.linalg.norm(center_line - p2, axis=1)
    i2 = np.argmin(d2)
    
    return s[i2] - s[i1]


def sort_conf_point(ego_pos, dict_lanelet_conf_point, cv, cv_s):
    ''' 给冲突点按照离自车的距离 由近到远 排序。
    params: 
        center_line: 道路中心线；
        s : 道路中心线累积距离;
        p1, p2: 点1， 点2
    returns:
        sorted_lanelet: conf_points 的下标排序
        i_ego: sorted_lanelet[i_ego]则是自车需要考虑的最近的lanelet
    '''
    conf_points = list(dict_lanelet_conf_point.values())
    lanelet_ids = np.array(list(dict_lanelet_conf_point.keys()))
    distance = []
    for conf_point in conf_points:
        distance.append(distance_lanelet(cv, cv_s, ego_pos, conf_point))
    distance = np.array(distance)
    id = np.argsort(distance)

    distance_sorted = distance[id]
    index = np.where(distance_sorted>0)[0]
    if len(index) == 0:
        i_ego  = len(distance)
    else:
        i_ego = index.min()
    
    sorted_lanelet = lanelet_ids[id]

    return sorted_lanelet, i_ego


def find_reference(s, ref_cv, ref_orientation,  ref_cv_len):
    ref_cv, ref_orientation,  ref_cv_len = np.array(ref_cv), np.array(ref_orientation), np.array(ref_cv_len)
    id = np.searchsorted(ref_cv_len, s)
    if id >= ref_orientation.shape[0]:
        print('end of reference line, please stop !')
        id =   ref_orientation.shape[0]-1
    return ref_cv[:, id], ref_orientation[id]



class IntersectionInfo():
    ''' 提取交叉路口的冲突信息
    '''
    def __init__(self, cl) -> None:
        '''
        params:
            cl: Conf_Lanelet类
        '''
        self.dict_lanelet_conf_point = {}                   # 地图信息。与自车轨迹存在直接相交的lanelet。(必定在路口内)
        for i in range(len(cl.id)):
            self.dict_lanelet_conf_point[cl.id[i]] = cl.conf_point[i]

        self.dict_lanelet_agent ={}                                 # 场景信息。直接冲突lanelet - > 离冲突点最近的agent
        self.dict_parent_lanelet = {}                               # 地图信息。父节点lanelet -> 子节点列表
        self.dict_lanelet_potential_agent = {}          
        self.sorted_lanelet = []
        self.i_ego = 0
        self.sorted_conf_agent = []     # 最终结果：他车重要度排序
        self.dict_agent_lanelets = {}

    def extend2list(self, lanelet_network):
        '''为了适应接口。暂时修改
        '''
        conf_potential_lanelets = []
        conf_potential_points = []
        ids = self.dict_lanelet_conf_point.keys
        conf_points =  self.dict_lanelet_conf_point.values

        for id, conf_point in zip(ids, conf_points):
            conf_lanlet = lanelet_network.find_lanelet_by_id(id)
            id_predecessors = conf_lanlet.predecessor
            # 排除没有父节点的情况
            if id_predecessors is not None:
                # 多个父节点
                for id_predecessor in id_predecessors:
                    conf_potential_lanelets.append(id_predecessor)
                    conf_potential_points.append(conf_point)
        return conf_potential_lanelets, conf_potential_points

class IntersectionPlanner():
    def __init__(self, scenario, state_init, goal) -> None:
        self.scenario = scenario
        self.state_init = state_init
        self.goal = goal


    def planner(self):
        '''轨迹规划器。返回轨迹
        Returns:
            trajectory: 自车轨迹。
        '''
        scenario = self.scenario
        lanelet_network = scenario.lanelet_network
        DT = scenario.dt
        

        # --------------- 检索地图，检查冲突lanelet和冲突点 ---------------------
        # 搜索结果： cl_info: ;conf_lanelet_potentials
        incoming_lanelet_id_sub = 50195
        direction_sub = 1
        # cl_info: 两个属性。id: 直接冲突lanelet的ID list。conf_point：对应的冲突点坐标list。
        cl_info = conf_lanelet_checker(lanelet_network, incoming_lanelet_id_sub, direction_sub)
        
        iinfo = IntersectionInfo(cl_info)
        iinfo.dict_parent_lanelet = potential_conf_lanelet_checkerv2(lanelet_network, cl_info)

        # ---------------- 运动规划 --------------
        ego_state = self.state_init
        
        # 计算车辆前进的参考轨迹
        cv1 = lanelet_network.find_lanelet_by_id(incoming_lanelet_id_sub).center_vertices
        cv2 = lanelet_network.find_lanelet_by_id(50209).center_vertices
        cv3 = lanelet_network.find_lanelet_by_id(50203).center_vertices
        cv =  np.concatenate((cv1, cv2,cv3), axis=0)
        ref_cv, ref_orientation,  ref_s = detail_cv(cv)

        T= [x for x in range(100)]
        # T = [70]
        s = distance_lanelet(ref_cv, ref_s, cv1[0,:],ego_state.position) # 已经有参考轨迹，直接计算行驶路程

        a_max = 3
        state_list = []
        state_list.append(ego_state)
        for  t in T:

            dict_lanelet_agent= self.conf_agent_checker(iinfo.dict_lanelet_conf_point, t)
            print('直接冲突车辆',dict_lanelet_agent)
            iinfo.dict_lanelet_agent = dict_lanelet_agent

            # 间接冲突车辆
            dict_lanelet_potential_agent = self.potential_conf_agent_checker(iinfo.dict_lanelet_conf_point, iinfo.dict_parent_lanelet, [50195,50209], t)
            print('间接冲突车辆',dict_lanelet_potential_agent)
            iinfo.dict_lanelet_potential_agent = dict_lanelet_potential_agent

            # 运动规划
            isConfFound = False
            # 冲突点排序
            iinfo.sorted_lanelet, iinfo.i_ego = sort_conf_point(ego_state.position, iinfo.dict_lanelet_conf_point, ref_cv, ref_s)

            # 按照冲突点先后顺序进行决策。找车，给冲突车辆排序
            sorted_conf_agent= [ ]
            dict_agent_lanelets = {}
            for i_lanelet in range(iinfo.i_ego, len(iinfo.sorted_lanelet)):
                lanelet_id = iinfo.sorted_lanelet[i_lanelet]
                # 直接冲突
                if lanelet_id in dict_lanelet_agent.keys():
                    sorted_conf_agent.append(iinfo.dict_lanelet_agent[lanelet_id])
                    dict_agent_lanelets[sorted_conf_agent[-1]] = [lanelet_id]
                else:
                    # 查找父节点
                    lanelet = lanelet_network.find_lanelet_by_id(lanelet_id)
                    for parent_lanelet_id in lanelet.predecessor:
                        if parent_lanelet_id not in dict_lanelet_potential_agent.keys():
                            # 如果是None, 没有父节点，也会进入该循环
                            continue
                        else:
                            sorted_conf_agent.append(iinfo.dict_lanelet_potential_agent[parent_lanelet_id])
                            if sorted_conf_agent[-1] not in dict_agent_lanelets.keys():                                
                                dict_agent_lanelets[sorted_conf_agent[-1]] = [parent_lanelet_id, lanelet_id]
                            continue
            iinfo.sorted_conf_agent =  sorted_conf_agent
            iinfo.dict_agent_lanelets = dict_agent_lanelets
            print('车辆重要性排序：', iinfo.sorted_conf_agent)
            print('对应车辆可能lanelet：', iinfo.dict_agent_lanelets)

            # 目前。根据未来两辆车进行决策。不够两辆车怎么搞？
            n_o = min(len(iinfo.sorted_conf_agent), 2)
            o_ids = []
            a = [ ]
            for i in range(n_o):
                o_ids.append(iinfo.sorted_conf_agent[i])
                lanelet_ids = iinfo.dict_agent_lanelets[o_ids[i]]
                conf_point = iinfo.dict_lanelet_conf_point[lanelet_ids[-1]]
                a.append(self.compute_acc4cooperate(ego_state, ref_cv, ref_s, conf_point, lanelet_ids, o_ids[i],t)) 

            ego_state, s = self.motion_planner(a,  ego_state, s, [ref_cv, ref_orientation, ref_s], t)
            # tmp_state = copy.deepcopy(ego_state)
            state_list.append(ego_state)

        # create the planned trajectory starting at time step 1
        ego_vehicle_trajectory = Trajectory(initial_time_step=1, state_list=state_list[1:])
        # create the prediction using the planned trajectory and the shape of the ego vehicle

        vehicle3 = parameters_vehicle3.parameters_vehicle3()
        ego_vehicle_shape = Rectangle(length=vehicle3.l, width=vehicle3.w)
        ego_vehicle_prediction = TrajectoryPrediction(trajectory=ego_vehicle_trajectory,
                                                    shape=ego_vehicle_shape)

        # the ego vehicle can be visualized by converting it into a DynamicObstacle
        ego_vehicle_type = ObstacleType.CAR
        ego_vehicle = DynamicObstacle(obstacle_id=100, obstacle_type=ego_vehicle_type,
                                    obstacle_shape=ego_vehicle_shape, initial_state=self.state_init,
                                    prediction=ego_vehicle_prediction)
        return ego_vehicle


    def motion_planner(self, a, ego_state0, s, ref_info, t): 
        ''''根据他车协作加速度，规划自己的运动轨迹；
        params:
            a: 协作加速度
        returns:
            ego_state: 自车下一时刻的状态
        '''
        if len(a) >1:
            a1 = a[0]
            a2 = a[1]
        elif len(a) ==1:
            a1 = a[0]
            a2 =a[0]
        else:
            a1 = 100
            a2= 100
        DT = self.scenario.dt
        a_max =3
        a_thre = 0          # 非交互式情况，协作加速度阈值(threshold) 设置为0
        if a1 < a_thre or a2<a_thre:
            print(' 避让这辆车', a1, a2)
            v0 = ego_state0.velocity
            v = v0 - a_max *DT
            if v<0:
                v = 0
            s += v*DT

        else:
            print(' 加速通过', a1, a2)
            v0 = ego_state0.velocity
            v = v0 + a_max *DT
            s += v*DT

        ref_cv, ref_orientation,  ref_s = ref_info
        position, orientation = find_reference(s,  ref_cv, ref_orientation,  ref_s )
        tmp_state = State()
        tmp_state.position = position
        tmp_state.velocity = v
        tmp_state.orientation = orientation
        tmp_state.time_step = t

        return tmp_state, s        


    def conf_agent_checker(self, dict_lanelet_conf_points, T):
        '''  找直接冲突点 conf_lanelets中最靠近冲突点的车，为冲突车辆
        params:
            dict_lanelet_conf_points: 字典。直接冲突点的lanelet_id->冲突点位置
            T: 仿真时间步长
        returns:
            [!!!若该lanelet上没有障碍物，则没有这个lanelet的key。]
            字典dict_lanelet_agent: lanelet-> obstacle_id。可以通过scenario.obstacle_by_id(obstacle_id)获得该障碍物。
           [option] 非必要字典dict_lanelet_d: lanelet - > distance。障碍物到达冲突点的距离。负数说明过了冲突点一定距离
        '''
        scenario = self.scenario
        lanelet_network = scenario.lanelet_network
        conf_lanelet_ids = list(dict_lanelet_conf_points.keys())            # 所有冲突lanelet列表

        dict_lanelet_agent = {}         # 字典。key: lanelet, obs_id ;
        dict_lanelet_d ={}          # 字典。key: lanelet, value: distacne .到冲突点的路程
            
        n_obs = len(scenario.obstacles)
        # 暴力排查场景中的所有车
        for i in range(n_obs):
            state =  scenario.obstacles[i].state_at_time(T)
            # 当前时刻这辆车可能没有
            if state is None:
                continue
            pos = scenario.obstacles[i].state_at_time(T).position
            lanelet_ids = lanelet_network.find_lanelet_by_position([pos])[0]
            # 可能在多条车道上，现在每个都做检查
            for lanelet_id in lanelet_ids:
                # 不能仅用位置判断车道。车的朝向也需要考虑?暂不考虑朝向。因为这样写不美。可能在十字路口倒车等
                lanelet = lanelet_network.find_lanelet_by_id(lanelet_id)
                res = lanelet.get_obstacles([ scenario.obstacles[i]], T)
                if  scenario.obstacles[i] not in res:
                    continue

                # 如果该车在 冲突lanelet上
                if lanelet_id in conf_lanelet_ids:
                    lanelet_center_line = lanelet_network.find_lanelet_by_id(lanelet_id).center_vertices

                    # 插值函数
                    lanelet_center_line, _,  lanelet_center_line_s = detail_cv(lanelet_center_line)

                    conf_point = dict_lanelet_conf_points[lanelet_id]
                    d_obs2conf_point = distance_lanelet(lanelet_center_line, lanelet_center_line_s, pos, conf_point)
                    
                    # 车辆已经通过冲突点，跳过循环
                    # 可能有问题...在冲突点过了一点点的车怎么搞？
                    if d_obs2conf_point< -2 - scenario.obstacles[i].obstacle_shape.length/2:
                        # 如果超过冲突点一定距离。不考虑该车
                        break
                    if lanelet_id not in dict_lanelet_d:
                        # 该lanelet上出现的第一辆车
                        dict_lanelet_d[lanelet_id] = d_obs2conf_point
                        dict_lanelet_agent[lanelet_id] = scenario.obstacles[i].obstacle_id
                    else:           
                        if d_obs2conf_point < dict_lanelet_d[lanelet_id]:
                            dict_lanelet_d[lanelet_id] = d_obs2conf_point
                            dict_lanelet_agent[lanelet_id] = scenario.obstacles[i].obstacle_id
            
        return dict_lanelet_agent


    def potential_conf_agent_checker(self, dict_lanelet_conf_point, dict_parent_lanelet,  ego_lanelets,T):
        '''找间接冲突lanelet.
        params:
            dict_lanelet_conf_point:
            dict_parent_lanelet: 间接冲突lanelet->子节点列表。
            T:
        returns:
            dict_lanelet_potential_agent: 间接冲突lanelet->冲突智能体列表。
        '''
        # 即使一辆车多个意图可能相撞，但是只用取一个值就行。任一个冲突点都是靠近终点。

        dict_parent_conf_point = {}         # 可能冲突lanelet -> 随意一个冲突点；因为越靠近终点的就是最需要的车辆。
        for parent, kids in dict_parent_lanelet.items():
            for kid in kids:
                if kid in dict_lanelet_conf_point.keys():
                    dict_parent_conf_point[parent] = dict_lanelet_conf_point[kid]
        
        # 删除前车影响：
        for ego_lanelet in ego_lanelets:
            if ego_lanelet in dict_parent_conf_point.keys():
                dict_parent_conf_point.pop(ego_lanelet)

        dict_lanelet_potential_agent = self.conf_agent_checker(dict_parent_conf_point, T)

        return dict_lanelet_potential_agent


    def compute_acc4cooperate(self, ego_state, ref_cv, ref_s, conf_point, conf_lanelet_ids,obstacle_id, T):
        '''计算单辆车的协作加速度。用于之后的运动规划。协作加速度为，自车匀速到达冲突点，他车同时到达该点需要的加速度
        params:
            ego_state: common-road state。起码包含属性position, v,
            ref_cv, ref_s: 自车参考轨迹中心线，累计距离。
            conf_points 冲突点
            obstacle_id: 
            conf_lanelet_ids: 他车到达冲突点的lanelet列表。间接冲突车辆可能会经过多个lanelet才能到达冲突点
            T: 仿真步长
        returns:
            a    # 协作的加速度
        '''
        scenario  = self.scenario
        pos = ego_state.position
        v =60/3.6

        
        t4ego2pass = []
        if v ==0:
            v = v+1
        d_ego2cp = distance_lanelet(ref_cv, ref_s, pos, conf_point)
        t4ego2pass.append(d_ego2cp / v)
        t4ego2pass = np.array(t4ego2pass)
        t_thre = 0.5
        t = t4ego2pass + t_thre

        conf_agent = scenario.obstacle_by_id(obstacle_id)
        state = conf_agent.state_at_time(T)
        p, v = state.position, state.velocity

        conf_cvs = []
        if not isinstance(conf_lanelet_ids, Iterable):
            conf_lanelet = scenario.lanelet_network.find_lanelet_by_id(conf_lanelet_ids)
            conf_cvs = conf_lanelet.center_vertices
        else:
            for conf_lanelet_id in conf_lanelet_ids:
                conf_lanelet = scenario.lanelet_network.find_lanelet_by_id(conf_lanelet_id)
                conf_cv = conf_lanelet.center_vertices
                conf_cvs.append(conf_cv)
            conf_cvs = np.concatenate(conf_cvs, axis=0)
                

        conf_cvs, _, conf_s = detail_cv(conf_cvs)

        s = distance_lanelet(conf_cvs, conf_s, p, conf_point)
        a = 2*(s-v*t)/(t**2)
        return a


if __name__=='__main__':
    #  下载 common road scenarios包。https://gitlab.lrz.de/tum-cps/commonroad-scenarios。修改为下载地址
    path_scenario_download = os.path.abspath('/home/tiecun/codes/commonroad/commonroad-scenarios/scenarios/hand-crafted')
    # 文件名
    id_scenario = 'ZAM_Tjunction-1_282_T-1'

    path_scenario =os.path.join(path_scenario_download, id_scenario + '.xml')
    # read in scenario and planning problem set
    scenario, planning_problem_set = CommonRoadFileReader(path_scenario).open()
    # retrieve the first planning problem in the problem set
    planning_problem = list(planning_problem_set.planning_problem_dict.values())[0]

    state_init = planning_problem.initial_state
    goal  = [0,0]

    ip = IntersectionPlanner(scenario, state_init, goal)
    ego_vehicle = ip.planner()


    # plt.figure(1)

    # plt.clf()
    # draw_parameters = {
    #     'time_begin': 0, 
    #     'scenario':
    #     { 'dynamic_obstacle': { 'show_label': True, },
    #         'lanelet_network':{'lanelet':{'show_label': False,  },} ,
    #     },
    # }

    # draw_object(scenario, draw_params=draw_parameters)
    # draw_object(planning_problem_set)
    # plt.gca().set_aspect('equal')
    # # plt.pause(0.01)
    # plt.show()

    # plot the scenario and the ego vehicle for each time step
    plt.figure(1)
    for i in range(0, 40):
        rnd = MPRenderer()
        scenario.draw(rnd, draw_params={'time_begin': i})
        ego_vehicle.draw(rnd, draw_params={'time_begin': i, 'dynamic_obstacle': {
            'vehicle_shape': {'occupancy': {'shape': {'rectangle': {
                'facecolor': 'r'}}}}}})
        planning_problem_set.draw(rnd)
        rnd.render()
        plt.pause(0.01)
