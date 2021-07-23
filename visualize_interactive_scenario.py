import os
from commonroad.common.file_reader import CommonRoadFileReader
import os, sys
from commonroad.common.file_reader import CommonRoadFileReader
sys.path.append('/home/thicv/codes/commonroad/commonroad-interactive-scenarios/')
from simulation.simulations import load_sumo_configuration
from sumocr.maps.sumo_scenario import ScenarioWrapper
from sumocr.interface.sumo_simulation import SumoSimulation
from utils import plot_lanelet_network
import matplotlib.pyplot  as plt
from commonroad.visualization.draw_dispatch_cr import draw_object


folder_scenarios = "/home/thicv/codes/commonroad/commonroad-scenarios/scenarios/scenarios_cr_competition/competition_scenarios_new/interactive/"
name_scenario = "DEU_Frankfurt-7_11_I-1"
interactive_scenario_path = os.path.join(folder_scenarios, name_scenario)

conf = load_sumo_configuration(interactive_scenario_path)
scenario_file = os.path.join(interactive_scenario_path, f"{name_scenario}.cr.xml")
scenario, planning_problem_set = CommonRoadFileReader(scenario_file).open()

ln = scenario.lanelet_network
# plot_lanelet_network(ln)
plt.clf()
draw_parameters = {
    'time_begin':1, 
    'scenario':
    { 'dynamic_obstacle': { 'show_label': True, },
        'lanelet_network':{'lanelet':{'show_label': True,  },} ,
    },
}

draw_object(scenario, draw_params=draw_parameters)
draw_object(planning_problem_set)
plt.gca().set_aspect('equal')
# plt.pause(0.001)
plt.show()