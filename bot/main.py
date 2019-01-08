import json
import time
from pathlib import Path
from typing import Dict, List

import sc2
from sc2.constants import *
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        self.last_scout_time = 0
        self.army_overlord_tag = 0
        self.resource_list: List[List] = None
        self.expansion_locs = {}

    def select_target(self):
        if self.known_enemy_structures.exists:
            return self.known_enemy_structures.closest_to(self.start_location).position
        return self.enemy_start_locations[0]

    async def on_step(self, iteration):
        larvae = self.units(LARVA)
        forces = self.units(ZERGLING) | self.units(HYDRALISK)

        self.calc_resource_list()
        self.calc_expansion_loc()

        if self.townhalls.amount <= 0:
            for unit in self.units(DRONE) | self.units(QUEEN) | forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            hq = self.townhalls.closest_to(self.start_location)

        for t in self.townhalls.ready:
            t: Unit = t

            if t.assigned_harvesters < t.ideal_harvesters:
                if self.can_afford(DRONE) and larvae.exists:
                    await self.do(larvae.random.train(DRONE))
                    return

            excess_worker = self.workers.closer_than(10, t.position)
            m = self.need_worker_mineral()
            if t.assigned_harvesters > t.ideal_harvesters and excess_worker.exists and m is not None:
                await self.do(excess_worker.random.gather(m))

            if (self.units(SPAWNINGPOOL).ready.exists and
                    t.is_ready and
                    t.noqueue and
                    not self.units(QUEEN).closer_than(10, t.position).exists and
                    self.can_afford(QUEEN)):
                await self.do(t.train(QUEEN))

            queen_nearby = self.units(QUEEN).idle.closer_than(10, t.position)
            if queen_nearby.exists:
                queen = queen_nearby.first
                abilities = await self.get_available_abilities(queen)
                if AbilityId.EFFECT_INJECTLARVA in abilities:
                    await self.do(queen(EFFECT_INJECTLARVA, t))

        t = self.nearby_enemies()
        actions = []
        if t is not None:
            for unit in forces:
                actions.append(unit.attack(t.position))
        elif self.supply_used > 190:
            target = self.select_target()

            army_overlord = self.units(OVERLORD).find_by_tag(self.army_overlord_tag)
            if army_overlord is None:
                army_overlord = self.units(OVERLORD).random
                self.army_overlord_tag = army_overlord.tag
            actions.append(
                army_overlord.move(target)
            )

            for unit in forces:
                unit: Unit = unit
                if not unit.is_attacking:
                    actions.append(unit.attack(target))
        else:
            far_h = self.townhalls.furthest_to(self.start_location)
            for unit in forces.further_than(10, far_h.position):
                if not unit.is_moving:
                    actions.append(unit.move(far_h.position.random_on_distance(5)))
        await self.do_actions(actions)

        # supply_cap does not include overload that is being built
        if (self.units(OVERLORD).amount + self.already_pending(OVERLORD)) * 8 - self.supply_used < 2:
            if self.can_afford(OVERLORD) and larvae.exists:
                await self.do(larvae.random.train(OVERLORD))
                return

        sp = self.units(SPAWNINGPOOL).ready
        if sp.exists:
            if self.already_pending_upgrade(ZERGLINGMOVEMENTSPEED) == 0 and self.can_afford(ZERGLINGMOVEMENTSPEED):
                await self.do(sp.first.research(ZERGLINGMOVEMENTSPEED))
            if not self.units(LAIR).exists and hq.noqueue:
                if self.can_afford(LAIR):
                    await self.do(hq.build(LAIR))

        hd = self.units(HYDRALISKDEN).ready
        if hd.exists:
            if self.already_pending_upgrade(EVOLVEMUSCULARAUGMENTS) == 0 and self.can_afford(EVOLVEMUSCULARAUGMENTS):
                await self.do(hd.first.research(EVOLVEMUSCULARAUGMENTS))
            if self.already_pending_upgrade(EVOLVEGROOVEDSPINES) == 0 and self.can_afford(EVOLVEGROOVEDSPINES):
                await self.do(hd.first.research(EVOLVEGROOVEDSPINES))
            if self.can_afford(HYDRALISK) and larvae.exists:
                await self.do(larvae.random.train(HYDRALISK))
                return

        if not (self.units(SPAWNINGPOOL).exists or self.already_pending(SPAWNINGPOOL) > 0):
            if self.can_afford(SPAWNINGPOOL):
                await self.build(SPAWNINGPOOL, near=hq)

        if self.should_expand() and self.resource_list is not None and len(self.resource_list) == 0:
            empty_expansions = set()
            for loc in self.expansion_locs:
                loc: Point2 = loc
                if self.townhalls.closer_than(self.EXPANSION_GAP_THRESHOLD, loc).amount == 0:
                    empty_expansions.add(loc)
            pos = self.start_location.closest(empty_expansions)
            print(pos)
            await self.do(self.workers.random.build(HATCHERY, pos))

        if self.units(LAIR).ready.exists:
            if not (self.units(HYDRALISKDEN).exists or self.already_pending(HYDRALISKDEN) > 0):
                if self.can_afford(HYDRALISKDEN):
                    await self.build(HYDRALISKDEN, near=hq)

        if self.units(OVERLORD).amount == 1:
            o: Unit = self.units(OVERLORD).first
            await self.do_actions([
                o.move(self.enemy_start_locations[0]),
                o.move(self.game_info.map_center, queue=True)
            ])

        if self.should_build_extractor():
            drone = self.workers.random
            target = self.state.vespene_geyser.closest_to(drone.position)
            await self.do(drone.build(EXTRACTOR, target))

        for a in self.units(EXTRACTOR).ready:
            if a.assigned_harvesters < a.ideal_harvesters:
                w = self.workers.closer_than(20, a)
                if w.exists:
                    await self.do(w.random.gather(a))

        for d in self.units(DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        if self.units(ZERGLING).amount < 20 and self.minerals - self.vespene > 500:
            if larvae.exists and self.can_afford(ZERGLING):
                await self.do(larvae.random.train(ZERGLING))
                return

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.townhalls.amount < 3:
            return self.units(EXTRACTOR).amount < self.townhalls.amount * 2 - 2
        else:
            return self.units(EXTRACTOR).amount < self.townhalls.amount * 2

    def nearby_enemies(self):
        for t in self.townhalls:
            t: Unit = t
            threats = self.known_enemy_units.closer_than(10, t.position)
            if threats.amount > 10:
                return threats.random
        return None

    def should_expand(self):
        if self.minerals < 300 or self.already_pending(HATCHERY) > 0:
            return False
        if self.units(SPAWNINGPOOL).exists and self.townhalls.amount < 2:
            return True
        if self.units(HYDRALISKDEN).exists and self.townhalls.amount < 3:
            return True
        total_ideal_harvesters = 0
        for t in self.townhalls.ready:
            t: Unit = t
            total_ideal_harvesters += t.ideal_harvesters
        return total_ideal_harvesters < 16 * 3

    def calc_resource_list(self):
        if self.resource_list is not None:
            return
        RESOURCE_SPREAD_THRESHOLD = 144
        all_resources = self.state.mineral_field | self.state.vespene_geyser
        # Group nearby minerals together to form expansion locations
        r_groups = []
        for mf in all_resources:
            mf_height = self.get_terrain_height(mf.position)
            for g in r_groups:
                if any(
                    mf_height == self.get_terrain_height(p.position)
                    and mf.position._distance_squared(p.position) < RESOURCE_SPREAD_THRESHOLD
                    for p in g
                ):
                    g.append(mf)
                    break
            else:  # not found
                r_groups.append([mf])
        # Filter out bases with only one mineral field
        self.resource_list = [g for g in r_groups if len(g) > 1]

    def calc_expansion_loc(self):
        if not self.resource_list or len(self.resource_list) == 0:
            return
        # distance offsets from a gas geysir
        offsets = [(x, y) for x in range(-9, 10) for y in range(-9, 10) if 75 >= x ** 2 + y ** 2 >= 49]
        # for every resource group:
        resources = self.resource_list.pop()
        # possible expansion points
        # resources[-1] is a gas geysir which always has (x.5, y.5) coordinates, just like an expansion
        possible_points = (
            Point2((offset[0] + resources[-1].position.x, offset[1] + resources[-1].position.y))
            for offset in offsets
        )
        # filter out points that are too near
        possible_points = [
            point
            for point in possible_points
            if all(
                point.distance_to(resource) >= (6 if resource in self.state.mineral_field else 7)
                for resource in resources
            )
        ]
        # choose best fitting point
        result = min(possible_points, key=lambda p: sum(p.distance_to(resource) for resource in resources))
        self.expansion_locs[result] = resources
