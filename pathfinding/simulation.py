"""Warehouse fleet demo: pickup -> drop, global kill-switch, then local coordination.
Run: python simulation.py
Outputs: sim_output.json consumed by warehouse_simulation.html.
"""
from __future__ import annotations
import json, random
from dataclasses import dataclass, asdict
from collections import deque
from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner

W=H=30; KILL_TICK=12; N=18
DIRS=[(1,0),(-1,0),(0,1),(0,-1)]

@dataclass
class Robot:
    robot_id:str; pos:tuple; start:tuple; pickup:tuple; drop:tuple
    state:str='TO_PICKUP'; carrying:bool=False; completed:bool=False
    battery:float=100.; priority:int=50; path:list=None; replans:int=0

def shelves():
    obs=set()
    for y in (5,10,15,20):
        for x in range(3,27):
            if x not in (7,14,21): obs.add((x,y))
    return obs

def bfs(start, goal, blocked):
    if start==goal:return [start]
    q=deque([start]); prev={start:None}
    while q:
        p=q.popleft()
        for dx,dy in DIRS:
            n=(p[0]+dx,p[1]+dy)
            if 0<=n[0]<W and 0<=n[1]<H and n not in blocked and n not in prev:
                prev[n]=p
                if n==goal:
                    out=[n]
                    while prev[out[-1]] is not None: out.append(prev[out[-1]])
                    return list(reversed(out))
                q.append(n)
    return [start]

def make_robots():
    random.seed(26123)
    starts=[(1,1),(28,1),(1,28),(28,28),(2,8),(27,8),(2,13),(27,13),(2,18),(27,18),(2,23),(27,23),(8,2),(14,2),(20,2),(8,27),(14,27),(20,27)]
    pickups=[(4,3),(25,3),(4,26),(25,26),(6,8),(23,8),(6,13),(23,13),(6,18),(23,18),(6,23),(23,23),(8,4),(14,4),(20,4),(8,25),(14,25),(20,25)]
    drops=[(25,24),(4,24),(25,4),(4,4),(22,12),(7,22),(22,17),(7,7),(22,22),(7,12),(22,7),(7,17),(20,26),(14,26),(8,26),(20,3),(14,3),(8,3)]
    return [Robot(f'AMR-{i+1:02d}',starts[i],starts[i],pickups[i],drops[i],battery=round(random.uniform(72,100),1),priority=random.randint(45,95),path=[]) for i in range(N)]

def run():
    obstacles=shelves(); robots=make_robots(); events=[]; frames=[]; kill=False; conflict_count=0
    # initial independent routes
    for r in robots:r.path=bfs(r.pos,r.pickup,obstacles)[1:]
    for tick in range(140):
        if tick==KILL_TICK:
            kill=True
            events.append({'tick':tick,'type':'KILL_SWITCH','text':'GLOBAL KILL SWITCH: central route controller offline. Robots must coordinate locally.'})
            for r in robots:
                if not r.completed:
                    events.append({'tick':tick,'type':'BROADCAST','robot':r.robot_id,'text':f'{r.robot_id}: LOCAL STATUS pos={r.pos}, state={r.state}, battery={r.battery}%'})
            # deliberately discard old paths; each robot locally computes from live map
            for r in robots:
                if not r.completed:
                    goal=r.drop if r.carrying else r.pickup
                    r.path=bfs(r.pos,goal,obstacles)[1:]; r.replans+=1
                    events.append({'tick':tick,'type':'LOCAL_REPLAN','robot':r.robot_id,'text':f'{r.robot_id}: calculated local route to {goal} and broadcast intent'})

        active=[r for r in robots if not r.completed]
        if not active:
            frames.append({'tick':tick,'robots':[snapshot(r) for r in robots]}); break

        # pre-move pickup/drop transitions
        for r in active:
            if r.state=='TO_PICKUP' and r.pos==r.pickup:
                r.carrying=True;r.state='TO_DROP';r.path=bfs(r.pos,r.drop,obstacles)[1:]
                events.append({'tick':tick,'type':'PICK','robot':r.robot_id,'text':f'{r.robot_id} picked BOX-{r.robot_id[-2:]} at {r.pickup}'})
            elif r.state=='TO_DROP' and r.pos==r.drop:
                r.carrying=False;r.completed=True;r.state='TASK_COMPLETED';r.path=[]
                events.append({'tick':tick,'type':'DROP','robot':r.robot_id,'text':f'{r.robot_id} dropped BOX-{r.robot_id[-2:]} at {r.drop}; TASK COMPLETED'})

        occupied={r.pos:r.robot_id for r in robots if not r.completed}
        intents={}
        for r in active:
            if r.completed: continue
            goal=r.drop if r.carrying else r.pickup
            # local replanning when path stale/blocked
            if not r.path or r.path[-1]!=goal:
                r.path=bfs(r.pos,goal,obstacles)[1:];r.replans+=1
            intents[r.robot_id]=r.path[0] if r.path else r.pos

        # local communication: same-target conflict, occupancy, and swaps
        approved={}; by_target={}
        for rid,tgt in intents.items():by_target.setdefault(tgt,[]).append(rid)
        ridmap={r.robot_id:r for r in robots}
        for tgt,rids in by_target.items():
            if len(rids)>1:
                conflict_count+=1
                winner=max(rids,key=lambda x:(ridmap[x].priority, ridmap[x].carrying, -int(x[-2:])))
                events.append({'tick':tick,'type':'NEGOTIATION','text':f'CONFLICT {tgt}: '+', '.join(rids)+f' requested same cell. {winner} wins priority; others locally replan.'})
                for rid in rids:
                    if rid==winner: approved[rid]=tgt
                    else:
                        rr=ridmap[rid]; dynamic=set(occupied)
                        dynamic.discard(rr.pos)
                        # avoid winner target and occupied robots for local detour
                        dynamic.add(tgt)
                        rr.path=bfs(rr.pos, rr.drop if rr.carrying else rr.pickup, obstacles|dynamic)[1:]
                        rr.replans+=1
                        approved[rid]=rr.path[0] if rr.path else rr.pos
                        events.append({'tick':tick,'type':'LOCAL_REPLAN','robot':rid,'text':f'{rid}: received conflict message, recalculated alternate path'})
            else: approved[rids[0]]=tgt

        # occupied cell / swap final safety and waiting
        final={}
        for rid,tgt in approved.items():
            r=ridmap[rid]; blocker=occupied.get(tgt)
            if blocker and blocker!=rid and approved.get(blocker, ridmap[blocker].pos)==tgt:
                # blocker staying: wait + local replan next tick
                final[rid]=r.pos
                r.state='WAITING_FOR_CELL'
                events.append({'tick':tick,'type':'WAIT','robot':rid,'text':f'{rid}: {tgt} occupied by {blocker}; WAITING and requesting clearance'})
            else: final[rid]=tgt
        # prevent swaps
        for a,ta in list(final.items()):
            for b,tb in list(final.items()):
                if a>=b:continue
                if ta==ridmap[b].pos and tb==ridmap[a].pos:
                    loser=min((a,b),key=lambda x:ridmap[x].priority)
                    final[loser]=ridmap[loser].pos
                    ridmap[loser].state='WAITING_FOR_CELL'
                    events.append({'tick':tick,'type':'NEGOTIATION','text':f'HEAD-ON SWAP {a}<->{b}; {loser} yields and locally replans'})

        for rid,tgt in final.items():
            r=ridmap[rid]
            if tgt!=r.pos:
                r.pos=tgt;r.battery=max(0,r.battery-0.25);r.state='TO_DROP' if r.carrying else 'TO_PICKUP'
                if r.path and r.path[0]==tgt:r.path.pop(0)
            elif r.state!='WAITING_FOR_CELL': r.state='NEGOTIATING'
        frames.append({'tick':tick,'robots':[snapshot(r) for r in robots]})

    result={'meta':{'grid':[30,30],'robots':N,'kill_tick':KILL_TICK,'conflicts_resolved':conflict_count,'all_completed':all(r.completed for r in robots)},'obstacles':[list(p) for p in obstacles],'frames':frames,'events':events}
    with open('sim_output.json','w') as f:json.dump(result,f,indent=2)
    print(json.dumps(result['meta'],indent=2))

def snapshot(r):
    return {'id':r.robot_id,'x':r.pos[0],'y':r.pos[1],'start':list(r.start),'pickup':list(r.pickup),'drop':list(r.drop),'state':r.state,'carrying':r.carrying,'completed':r.completed,'battery':round(r.battery,1),'priority':r.priority,'replans':r.replans}
if __name__=='__main__':run()
