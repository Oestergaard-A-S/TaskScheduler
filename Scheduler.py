from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from ortools.sat.python import cp_model

@dataclass
class Resource:
    id: int
    availability_slots: List = field(default_factory=list)

    def __eq__(self, other):
        if isinstance(other, Resource):
            return self.id == other.id
        return NotImplemented
    
    def __hash__(self):
        return hash(self.id)

@dataclass
class ResourceGroup:
    resource_group_id: int
    resources: List[Resource]

    def __eq__(self, other):
        if isinstance(other, ResourceGroup):
            return self.resource_group_id == other.resource_group_id
        return NotImplemented
    
    def __hash__(self):
        return hash(self.resource_group_id)
    
@dataclass
class Task:
    id: int
    duration: int
    priority: int
    resource_group: ResourceGroup
    predecessors: Optional[List['Task']] = field(default_factory=list)

    def __eq__(self, other):
        if isinstance(other, Task):
            return self.id == other.id
        return NotImplemented

    def __hash__(self):
        return hash(self.id)
    
    def get_resources(self):
        return self.resource_group.resources

class Scheduler:
    def __init__(self, tasks, horizon):
        self.tasks = tasks
        self.horizon = horizon
        self.resource_bools = None
        self.subtask_vars = None

    def schedule(self):
        tasks = self.tasks
        horizon = self.horizon

        resource_groups = set(task.resource_group for task in tasks)
        resources = set(resource for resource_group in resource_groups for resource in resource_group.resources) 
        self.resource_groups = resource_groups
        self.resources = resources

        availability_slots = [
            {"slot_id" : 0, "start":0, "end":4}, # duration 1
            {"slot_id" : 1, "start":5, "end":10}, # duration 2
            {"slot_id" : 2, "start":15, "end":20}, # duration 3
            {"slot_id" : 3, "start":22, "end":50} # duration 4
        ]

        model = cp_model.CpModel()

        # create resource bools
        resource_bools = {}
        for task in tasks:
            for resource in task.get_resources():
                resource_bools[task.id, resource.id] = model.NewBoolVar(f'x[{task.id},{resource.id}]')
        
        self.resource_bools = resource_bools

        # constrain tasks to one resource
        for task in tasks:
            model.AddExactlyOne([resource_bools[task.id, resource.id] for resource in task.get_resources()])

        # subtaks 
        subtask_vars = {(task.id,resource.id): [] for task in tasks for resource in task.get_resources()}

        # Create subtasks for each availability slot
        for task in tasks:
            task.id = task.id
            task_duration = task.duration
            for resource in task.get_resources():
                subtask_durations = []
                for subtask_id, slot in enumerate(resource.availability_slots):

                    # create variables
                    start = model.NewIntVar(slot["start"], slot["end"], f'start_{slot["start"]}')
                    end = model.NewIntVar(slot["start"], slot["end"], f'end_{slot["end"]}')
                    duration = model.NewIntVar(0, task_duration, f'duration_{slot["slot_id"]}')
                    subtask_durations.append(duration)
                    interval = model.NewIntervalVar(start, duration, end, f'interval_{task.id, slot["slot_id"]}')

                    # Create a BoolVar to check if slot is task start
                    is_task_start = model.NewBoolVar(f'task_start_{task.id}_{slot["slot_id"]}')
                    model.Add(sum(subtask_durations) == duration).OnlyEnforceIf((is_task_start))
                    model.Add(sum(subtask_durations) != duration).OnlyEnforceIf(is_task_start.Not())

                    # Create a BoolVar to check if task has ended
                    is_task_end = model.NewBoolVar(f'task_ended_{task.id}_{slot["slot_id"]}')
                    model.Add(sum(subtask_durations) == task_duration).OnlyEnforceIf(is_task_end)
                    model.Add(sum(subtask_durations) != task_duration).OnlyEnforceIf(is_task_end.Not())

                    # Create a BoolVar if duration is 0
                    duration_is_zero = model.NewBoolVar(f'duration_is_zero_{task.id}_{slot["slot_id"]}')
                    model.Add(duration == 0).OnlyEnforceIf(duration_is_zero)
                    model.Add(duration != 0).OnlyEnforceIf(duration_is_zero.Not())

                    # Ensure is_in_progress is true when subtask_durations is between > 0 and 10
                    is_in_progress = model.NewBoolVar(f'task_started_{task.id}_{slot["slot_id"]}')
        
                    model.Add(sum(subtask_durations) == task_duration).OnlyEnforceIf(is_in_progress)
                    model.Add(sum(subtask_durations) != task_duration).OnlyEnforceIf(is_in_progress.Not())
                    
                    # Ensure subtasks are continuous 
                    # if task is in progress it should fill the whole slot
                    model.Add(start == slot["start"]).OnlyEnforceIf((is_in_progress,is_task_end.Not(), is_task_start.Not()))
                    model.Add(end == slot["end"]).OnlyEnforceIf((is_in_progress,is_task_end.Not(), is_task_start.Not()))

                    # if task is starting but not ending
                    model.Add(end == slot["end"]).OnlyEnforceIf((is_task_start, is_task_end.Not()))

                    # if task is ending but not starting
                    model.Add(start == slot["start"]).OnlyEnforceIf((is_task_end,is_task_start.Not()))        

                    # Add the subtask to the list
                    subtask_vars[task.id,resource.id].append({
                        "subtask_id" : subtask_id,
                        "slot_id": slot["slot_id"],
                        "start" : start,
                        "duration" : duration,
                        "end" : end,
                        "interval" : interval,
                        "is_task_start" : is_task_start,
                        "is_in_progress" : is_in_progress,
                        "is_task_end" : is_task_end,
                        "duration_is_zero" : duration_is_zero
                    })

                # Add constraint to enforce the sum of durations of subtasks to be equal to task_duration
                model.Add(sum(subtask_durations) == task_duration).OnlyEnforceIf(resource_bools[task.id,resource.id])
                model.Add(sum(subtask_durations) == 0).OnlyEnforceIf(resource_bools[task.id,resource.id].Not())

        self.subtask_vars = subtask_vars

        # Add NoOverlap constraint for each resource.
        resource_intervals = {resource.id: [] for resource in resources}
        for (task_id, resource_id), subtask_list in subtask_vars.items():
            for subtask in subtask_list:
                optional_interval = model.NewOptionalIntervalVar(
                    start = subtask["start"], 
                    size = subtask["duration"], 
                    end = subtask["end"], 
                    is_present = resource_bools[task_id, resource_id],
                    name = f'resource_interval_{resource}_{task_id, subtask["subtask_id"]}'
                    )
                resource_intervals[resource_id].append(optional_interval)

        for resource in resources:
            model.AddNoOverlap(resource_intervals[resource.id])

        # object var to reduce makespan
        # only consider ends with duration > 0
        optional_ends = []
        for (task_id, resource_id), subtask_list in subtask_vars.items():
            for subtask in subtask_list:
                end_var = model.NewIntVar(0, horizon, f'end_var_{task_id}_{resource_id}_{subtask["subtask_id"]}')
                model.AddElement(subtask["duration_is_zero"], [subtask["end"],0], end_var)
                optional_ends.append(end_var)

        obj_var = model.NewIntVar(0, horizon, 'makespan')
        model.AddMaxEquality(obj_var, optional_ends)
        model.Minimize(obj_var)
        
        # Create a solver and solve the model
        solver = cp_model.CpSolver()
        status = solver.Solve(model)

        # Check the solver status and print the solution
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(f"Makespan = {solver.ObjectiveValue()}")
            for task in tasks:
                for resource in task.get_resources():
                    if solver.BooleanValue(resource_bools[task.id, resource.id]):

                        print(f"Assigned to resource {resource.id}")
                        for subtask in subtask_vars[task.id, resource.id]:
                            # if solver.Value(subtask['duration']) > 0:
                                print(
                                    f"Task {(task.id, subtask['subtask_id'])}: "
                                    f"starts: {solver.Value(subtask['start'])}, "
                                    f"end: {solver.Value(subtask['end'])}, "
                                    f"duration: {solver.Value(subtask['duration'])}, "
                                    f"bools: {[solver.Value(subtask[field]) for field in ['is_task_start', 'is_in_progress', 'is_task_end', 'duration_is_zero']]}"
                                    )
        else:
            print("No solution found.")