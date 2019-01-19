import json
import math
from pathlib import Path
from typing import List, Dict, Set, Optional

import sc2
from sc2 import Race
from sc2.constants import *
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        super().__init__()
        self.last_scout_time = 0
        self.scout_units = set()
        self.resource_list: List[List] = None
        self.time_table = {}
        self.units_health = {}
        self.units_attacked: List[Unit] = []
        self.creep_queen_tag = 0
        self.hq: Unit = None
        self.all_in = False
        self.enemy_unit_history: Dict[UnitTypeId, Set[int]] = {}

        # enemy stats
        self.last_enemy_time = 0
        self.last_enemy_count = 0
        self.enemy_insight_frames = 0
        self.last_enemy_positions = []
        self.enemy_expansions: Units = None

        self.build_order = []
        self.production_order = []

    def _prepare_first_step(self):
        sc2.BotAI._prepare_first_step(self)
        self.expansion_locations.keys()

    async def on_step(self, iteration):

        self.production_order = []
        # enemy info
        self.calc_enemy_info()

        forces = (self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units) | self.units(UnitTypeId.BANELING) |
                  self.units(UnitTypeId.HYDRALISK) | self.units(UnitTypeId.ROACH).tags_not_in(self.scout_units) |
                  self.units(UnitTypeId.MUTALISK) | self.units(UnitTypeId.OVERSEER))
        half_size = self.start_location.distance_to(self.game_info.map_center)

        # if i don't even have a townhall
        # this has to be there because sometimes `self.townhalls` returns nothing even though there're clearly townhalls
        if not self.townhalls.exists:
            for unit in self.units(UnitTypeId.DRONE) | self.units(UnitTypeId.QUEEN) | forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            self.hq = self.townhalls.closest_to(self.start_location)
            far_townhall: Unit = self.townhalls.closest_to(self.game_info.map_center)
            rally_point: Point2 = far_townhall.position.towards(self.game_info.map_center, 4)

        is_terran = self.enemy_race == Race.Terran or self.known_enemy_units.of_type({
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.PLANETARYFORTRESS,
            UnitTypeId.SCV,
            UnitTypeId.MARINE,
            UnitTypeId.REAPER,
            UnitTypeId.MARAUDER,
            UnitTypeId.SIEGETANK,
            UnitTypeId.CYCLONE,
            UnitTypeId.HELLION,
            UnitTypeId.SUPPLYDEPOT,
            UnitTypeId.BARRACKS,
            UnitTypeId.REFINERY,
        }).exists

        if is_terran:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.BANELINGNEST,
                UnitTypeId.HYDRALISKDEN,
                UnitTypeId.EVOLUTIONCHAMBER,
            ]
        else:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.ROACHWARREN,
                UnitTypeId.HYDRALISKDEN,
                UnitTypeId.EVOLUTIONCHAMBER,
            ]

        # supply_cap does not include overload that is being built
        est_supply_cap = (self.count_unit(UnitTypeId.OVERLORD)) * 8 + self.townhalls.ready.amount * 6
        est_supply_left = est_supply_cap - self.supply_used
        if self.units(UnitTypeId.OVERLORD).amount == 1 and self.townhalls.amount < 2:
            build_overlord = False
        elif self.units(UnitTypeId.OVERLORD).amount <= 4:
            build_overlord = est_supply_left < 3
        else:
            build_overlord = est_supply_left < 8

        if build_overlord and est_supply_cap <= 200:
            await self.train(UnitTypeId.OVERLORD)

        # attacks
        actions = []
        for x in self.units_attacked:
            workers_nearby = self.workers.closer_than(5, x.position).filter(lambda wk: not wk.is_attacking)
            enemy_nearby = self.alive_enemy_units().closer_than(5, x.position)
            if not enemy_nearby.exists:
                continue
            if x.type_id == UnitTypeId.DRONE:
                another_townhall = self.townhalls.further_than(25, x.position)
                if forces.amount > enemy_nearby.amount and another_townhall.exists and self.townhalls.ready.amount > 3:
                    actions.append(x.move(another_townhall.first.position))
                elif workers_nearby.amount > 2:
                    actions.append(x.attack(enemy_nearby.first))
                    for w in workers_nearby:
                        w: Unit = w
                        if not w.is_attacking:
                            actions.append(w.attack(enemy_nearby.first))
            elif x.is_structure:
                if x.build_progress < 1 and x.health_percentage < 0.1:
                    actions.append(x(AbilityId.CANCEL))
            elif x.type_id == UnitTypeId.SWARMHOSTMP:
                actions.append(x.move())
            elif forces.closer_than(10, x.position).amount > self.alive_enemy_units().closer_than(10,
                                                                                                  x.position).amount:
                actions.append(x.attack(enemy_nearby.first))
            else:
                actions.append(x.move(rally_point))

        enemy_nearby = self.enemy_nearby()
        if enemy_nearby:
            for unit in forces:
                unit: Unit = unit
                # fight within spinecrawler
                t = self.target_position(unit)
                if t is not None and t.distance_to(self.start_location) > half_size:
                    continue
                if self.units(UnitTypeId.SPINECRAWLER).ready.exists and \
                        self.units(UnitTypeId.SPINECRAWLER).closest_distance_to(rally_point) < 15 and \
                        self.units(UnitTypeId.SPINECRAWLER).closest_distance_to(unit.position) < 15:
                    actions.append(unit.move(self.units(UnitTypeId.SPINECRAWLER).closest_to(unit.position)))
                else:
                    actions.append(unit.attack(enemy_nearby.position))
            for unit in self.units(UnitTypeId.SWARMHOSTMP).ready:
                abilities = (await self.get_available_abilities([unit]))[0]
                if AbilityId.EFFECT_SPAWNLOCUSTS in abilities and enemy_nearby.position.distance_to(unit.position) < 10:
                    actions.append(unit(AbilityId.EFFECT_SPAWNLOCUSTS, enemy_nearby.position))
        elif self.supply_used > 190:
            target = self.select_target()
            for unit in forces:
                actions.append(unit.attack(target))
        else:
            for unit in forces.further_than(10, far_townhall.position):
                if not unit.is_moving and (not self.last_enemy_positions or unit.position.distance_to_closest(
                        self.last_enemy_positions) > 10):
                    actions.append(unit.move(rally_point))
        swarmhost = self.units(UnitTypeId.SWARMHOSTMP).ready.idle
        sa = []
        if not enemy_nearby and swarmhost.amount >= 5:
            for s in swarmhost:
                s: Unit = s
                abilities = (await self.get_available_abilities([s]))[0]
                if self.enemy_expansions.exists and AbilityId.EFFECT_SPAWNLOCUSTS in abilities:
                    closest_exp = self.enemy_expansions.closest_to(s.position)
                    sa.append(s.move(closest_exp.position.towards(self.start_location, 20), queue=True))
                    sa.append(s(AbilityId.EFFECT_SPAWNLOCUSTS, closest_exp.position, queue=True))
                    sa.append(s.move(rally_point, queue=True))
        if len(sa) >= 15:
            actions.extend(sa)
        await self.do_actions(actions)
        # counter timing attack
        if await self.defend_early_rush():
            return
        if await self.defend_cannon_rush():
            return

        if self.count_unit(UnitTypeId.SPINECRAWLER) <= 0 and self.townhalls.ready.amount > 1:
            await self.build(UnitTypeId.SPINECRAWLER,
                             near=rally_point,
                             random_alternative=False)

        # economy
        for t in self.townhalls.ready:
            t: Unit = t
            excess_worker = self.workers.closer_than(10, t.position)
            m = self.need_worker_mineral()
            if t.assigned_harvesters > t.ideal_harvesters and excess_worker.exists and m is not None:
                await self.do(excess_worker.random.gather(m))
            queen_nearby = await self.inject_larva(t)
            if self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag) is None and queen_nearby.amount > 1:
                self.creep_queen_tag = queen_nearby[1].tag
            if self.townhalls.amount >= 3:
                if not self.units(UnitTypeId.SPORECRAWLER).closer_than(10, t.position).exists and \
                        self.already_pending(UnitTypeId.SPORECRAWLER) == 0:
                    await self.build(UnitTypeId.SPORECRAWLER,
                                     near=t.position.towards(self.state.mineral_field.closest_to(t).position, 3),
                                     random_alternative=False)

        if (self.count_unit(UnitTypeId.DRONE) < self.townhalls.amount * 16 + self.units(
                UnitTypeId.EXTRACTOR).amount * 3 or self.townhalls.ready.amount == 1) \
                and self.count_unit(UnitTypeId.DRONE) < 76:
            if forces.amount >= self.last_enemy_count or self.townhalls.amount < 2:
                self.production_order.append(UnitTypeId.DRONE)
            else:
                self.production_order.extend([UnitTypeId.HYDRALISK, UnitTypeId.ROACH, UnitTypeId.ZERGLING])

        # production queue
        # roach and hydra
        if self.units(UnitTypeId.ROACHWARREN).ready.exists and not self.units(
                UnitTypeId.HYDRALISKDEN).ready.exists and self.units(UnitTypeId.ROACH).amount < 10:
            self.production_order.append(UnitTypeId.ROACH)
        elif self.units(UnitTypeId.HYDRALISKDEN).ready.exists and self.units(UnitTypeId.HYDRALISK).amount < 20:
            self.production_order.append(UnitTypeId.HYDRALISK)
        elif self.units(UnitTypeId.ROACH).amount < 10:
            self.production_order.extend([UnitTypeId.HYDRALISK, UnitTypeId.ROACH])
        else:
            self.production_order.extend([UnitTypeId.HYDRALISK])
        # swarm host
        if self.units(UnitTypeId.INFESTATIONPIT).ready.exists and self.count_unit(UnitTypeId.SWARMHOSTMP) < 10:
            if self.supply_used > 150:
                self.production_order = [UnitTypeId.SWARMHOSTMP]
            else:
                self.production_order.insert(0, UnitTypeId.SWARMHOSTMP)
        # zerglings
        zergling_amount = self.units(UnitTypeId.ZERGLING).amount + 2 * self.already_pending(UnitTypeId.ZERGLING)
        if self.townhalls.ready.amount == 1 and zergling_amount < 6 + self.state.units(UnitTypeId.XELNAGATOWER).amount:
            self.production_order.insert(0, UnitTypeId.ZERGLING)
        elif zergling_amount < 12:
            self.production_order.append(UnitTypeId.ZERGLING)
        if self.already_pending_upgrade(UpgradeId.ZERGLINGATTACKSPEED) == 1 and zergling_amount < 30:
            self.production_order.append(UnitTypeId.ZERGLING)
        if UnitTypeId.BANELINGNEST in self.build_order and (zergling_amount < self.townhalls.ready.amount * 10):
            self.production_order.append(UnitTypeId.ZERGLING)
        # banelings
        if self.units(UnitTypeId.BANELINGNEST).ready.exists and self.units(UnitTypeId.ZERGLING).exists:
            if (self.townhalls.ready.amount == 2 and self.count_unit(UnitTypeId.BANELING) < 5) or \
                    (self.townhalls.ready.amount == 3 and self.count_unit(UnitTypeId.BANELING) < 10) or \
                    (self.townhalls.ready.amount >= 4 and self.count_unit(UnitTypeId.BANELING) < 20):
                await self.do(self.units(UnitTypeId.ZERGLING).closest_to(self.start_location)(
                    AbilityId.MORPHZERGLINGTOBANELING_BANELING))

        # lair upgrade
        if not self.units(UnitTypeId.LAIR).exists and \
                not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.LAIR, all_units=True) == 0 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                (self.supply_used > 100 and UnitTypeId.ROACHWARREN in self.build_order or
                 self.townhalls.amount >= 3 and UnitTypeId.BANELINGNEST in self.build_order) and \
                self.can_afford_or_change_production(UnitTypeId.LAIR):
            await self.do(self.hq.build(UnitTypeId.LAIR))

        # hive upgrade
        if not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.HIVE, all_units=True) == 0 and \
                self.units(UnitTypeId.INFESTATIONPIT).ready.exists and \
                self.can_afford_or_change_production(UnitTypeId.HIVE):
            await self.do(self.hq.build(UnitTypeId.HIVE))

        if self.enemy_expansions.exists:
            await self.call_every(self.scout_expansions, 2 * 60)
        else:
            await self.call_every(self.scout_expansions, 60)
        await self.call_every(self.scout_watchtower, 60)
        await self.fill_creep_tumor()
        await self.make_overseer()

        # expansion
        if self.should_expand() and self.can_afford_or_change_production(UnitTypeId.HATCHERY):
            await self.expand_now(None, 2)

        # first overlord scout
        if self.units(UnitTypeId.OVERLORD).amount == 1:
            o: Unit = self.units(UnitTypeId.OVERLORD).first
            exps = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            await self.do_actions([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 18)),
                o.move(exps[1].towards(self.game_info.map_center, 10), queue=True),
                o.move(self.game_info.map_center, queue=True)
            ])

        # second overlord scout
        o: Units = self.units(UnitTypeId.OVERLORD).idle
        if self.units(UnitTypeId.OVERLORD).amount == 2 and o.exists:
            await self.do(o.first.move(self.start_location.towards(self.game_info.map_center, 10), queue=True))

        # extractor and gas gathering
        if self.should_build_extractor():
            drone = self.workers.random
            target = self.state.vespene_geyser.closest_to(drone.position)
            if self.townhalls.ready.closest_distance_to(target.position) < 10:
                await self.do(drone.build(UnitTypeId.EXTRACTOR, target))
        for a in self.units(UnitTypeId.EXTRACTOR).ready:
            if a.assigned_harvesters < a.ideal_harvesters:
                w: Units = self.workers.closer_than(20, a)
                if w.exists:
                    await self.do(w.random.gather(a))
            if a.assigned_harvesters > a.ideal_harvesters:
                for w in self.workers.closer_than(2.5, a):
                    await self.do(w.gather(self.state.mineral_field.closest_to(w)))

        # overlord speed
        if self.units(UnitTypeId.LAIR).ready.exists and \
                self.already_pending_upgrade(UpgradeId.OVERLORDSPEED) == 0 and \
                self.can_afford_or_change_production(UpgradeId.OVERLORDSPEED):
            await self.do(self.hq.research(UpgradeId.OVERLORDSPEED))

        # drone
        for d in self.units(UnitTypeId.DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        await self.build_building()
        await self.upgrade_building()
        await self.produce_unit()

    async def fill_creep_tumor(self):
        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        creep_queen = self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag)
        # make creep queen
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                UnitTypeId.QUEEN not in self.production_order and \
                self.townhalls.amount >= 3 and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN) and \
                self.count_unit(UnitTypeId.QUEEN) <= self.townhalls.ready.amount:
            await self.do(self.townhalls.ready.furthest_to(self.start_location).train(UnitTypeId.QUEEN))
        if creep_queen is not None and creep_queen.is_idle:
            abilities = await self.get_available_abilities(creep_queen)
            if AbilityId.BUILD_CREEPTUMOR_QUEEN in abilities:
                t = self.townhalls.ready.furthest_to(self.start_location).position.random_on_distance(6)
                if creep_tumors.exists:
                    ct = creep_tumors.furthest_to(self.start_location).position.random_on_distance(10)
                    if ct.distance2_to(self.start_location) > t.distance2_to(self.start_location):
                        t = ct
                if self.has_creep(t) and self.can_place_creep_tumor(t):
                    await self.do(creep_queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, t))
        actions = []
        available_creep_tumors = self.units(UnitTypeId.CREEPTUMORBURROWED)
        if available_creep_tumors.exists:
            abilities: List[List[AbilityId]] = await self.get_available_abilities(available_creep_tumors)
            for i, a in enumerate(abilities):
                if AbilityId.BUILD_CREEPTUMOR_TUMOR in a:
                    u: Unit = available_creep_tumors[i]
                    t = self.calc_creep_tumor_position(u)
                    if t is not None:
                        actions.append(u(AbilityId.BUILD_CREEPTUMOR_TUMOR, t))
        await self.do_actions(actions)

    async def inject_larva(self, townhall: Unit):
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                townhall.is_ready and townhall.noqueue and \
                self.units(UnitTypeId.QUEEN).closer_than(10, townhall.position).amount == 0 and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN):
            await self.do(townhall.train(UnitTypeId.QUEEN))
        queen_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(10, townhall.position)
        if queen_nearby.tags_not_in({self.creep_queen_tag}).amount > 0:
            queen = queen_nearby.tags_not_in({self.creep_queen_tag}).first
            abilities = await self.get_available_abilities(queen)
            if AbilityId.EFFECT_INJECTLARVA in abilities:
                await self.do(queen(AbilityId.EFFECT_INJECTLARVA, townhall))
        return queen_nearby

    def calc_creep_tumor_position(self, u: Unit):
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi / 4)
            if self.can_place_creep_tumor(t):
                return t
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi / 2)
            if self.can_place_creep_tumor(t):
                return t
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi)
            if self.can_place_creep_tumor(t):
                return t
        return None

    def can_place_creep_tumor(self, t: Point2) -> bool:
        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        exp_points = list(self.expansion_locations.keys())
        return not creep_tumors.closer_than(10, t).exists and t.position.distance_to_closest(exp_points) > 5

    async def upgrade_building(self):
        if self.workers.collecting.amount < 32:
            return
        for b in self.build_order:
            u = self.units(b).ready
            if u.exists and u.first.is_idle:
                abilities = await self.get_available_abilities(u.first, ignore_resource_requirements=True)
                if len(abilities) > 0 and self.can_afford_or_change_production(abilities[0]):
                    await self.do(u.first(abilities[0]))

    async def build_building(self):
        for i, b in enumerate(self.build_order):
            for t in self.townhalls.sorted_by_distance_to(self.start_location):
                t: Unit = t
                p = t.position.random_on_distance(10)
                if (i == 0 or self.units(self.build_order[i - 1]).exists) and self.should_build(
                        b) and self.is_location_safe(p):
                    if b == UnitTypeId.ROACHWARREN and self.workers.amount < 16 * 2:
                        return
                    if b == UnitTypeId.BANELINGNEST and self.workers.amount < 16 * 2:
                        return
                    await self.build(b, near=p)
                    return
        if self.should_build(UnitTypeId.INFESTATIONPIT) and \
                self.count_unit(UnitTypeId.HYDRALISK) >= 10 and \
                self.units(UnitTypeId.LAIR).ready.exists and \
                self.can_afford_or_change_production(UnitTypeId.INFESTATIONPIT):
            await self.build(UnitTypeId.INFESTATIONPIT, near=self.hq.position.random_on_distance(10))

        if self.count_unit(UnitTypeId.EVOLUTIONCHAMBER) == 1 and self.supply_used > 100:
            await self.build(UnitTypeId.EVOLUTIONCHAMBER, near=self.hq.position.random_on_distance(10))

    def should_build(self, b):
        return not self.units(b).exists and self.already_pending(b) == 0 and self.can_afford(b)

    def count_unit(self, unit_type: UnitTypeId) -> int:
        return self.units(unit_type).amount + self.already_pending(unit_type, all_units=True)

    def select_target(self):
        if self.known_enemy_structures.exists:
            target = self.known_enemy_structures.furthest_to(self.enemy_start_locations[0])
            return target.position
        return self.enemy_start_locations[0]

    def enemy_nearby(self):
        for t in self.townhalls.ready:
            t: Unit = t
            e = self.alive_enemy_units().closer_than(20, t.position)
            if e.exists:
                return e.closest_to(t.position)
        return None

    def alive_enemy_units(self) -> Units:
        return self.known_enemy_units.exclude_type({
            UnitTypeId.OVERLORD,
            UnitTypeId.OVERSEER,
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
        }).filter(lambda u: u.health > 0)

    def calc_enemy_info(self):
        self.last_enemy_count = 0
        self.last_enemy_positions = []
        self.enemy_expansions = self.known_enemy_structures.of_type({
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.PLANETARYFORTRESS
        })
        has_enemy = False
        for t in self.units.structure:
            t: Unit = t
            threats = self.alive_enemy_units().closer_than(20, t)
            self.last_enemy_count += threats.amount
            if threats.exists:
                self.last_enemy_positions.append(threats.closest_to(t).position)
                has_enemy = True
        for e in self.known_enemy_units:
            e: Unit = e
            if e.type_id not in self.enemy_unit_history:
                self.enemy_unit_history[e.type_id] = set()
            self.enemy_unit_history[e.type_id].add(e.tag)

        if has_enemy:
            self.enemy_insight_frames += 1
            self.last_enemy_time = self.time
        else:
            self.enemy_insight_frames = 0

        def not_full_health(u: Unit) -> bool:
            return u.health < u.health_max

        self.units_attacked = []
        for w in self.units.filter(not_full_health):
            w: Unit = w
            if w.tag in self.units_health and w.health > self.units_health[w.tag]:
                self.units_attacked.append(w)
            self.units_health[w.tag] = w.health

    async def produce_unit(self):
        if self.supply_left == 0:
            return
        for u in self.production_order:
            await self.train(u)

    async def train(self, u):
        lv = self.units(UnitTypeId.LARVA)
        if lv.exists and self.can_afford(u):
            await self.do(lv.random.train(u))

    async def call_every(self, func, seconds):
        if func.__name__ not in self.time_table:
            self.time_table[func.__name__] = 0
        if self.time - self.time_table[func.__name__] > seconds:
            await func()

    def can_afford_or_change_production(self, u):

        def remove_if_exists(l, i):
            if i in l:
                l.remove(i)

        can_afford = self.can_afford(u)
        if not can_afford.can_afford_minerals:
            self.production_order = []
        if not can_afford.can_afford_vespene:
            remove_if_exists(self.production_order, UnitTypeId.ROACH)
            remove_if_exists(self.production_order, UnitTypeId.HYDRALISK)
            remove_if_exists(self.production_order, UnitTypeId.MUTALISK)
            remove_if_exists(self.production_order, UnitTypeId.SWARMHOSTMP)
        return can_afford

    def potential_scout_units(self):
        if self.supply_used > 190 and self.already_pending(UpgradeId.OVERLORDSPEED) == 1:
            scouts = self.units(UnitTypeId.OVERLORD).tags_not_in(self.scout_units)
        else:
            scouts = self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units)
        if not scouts.exists:
            scouts = self.units(UnitTypeId.OVERLORD)
        return scouts

    async def scout_expansions(self):
        if self.townhalls.amount <= 2 and not self.units(UnitTypeId.SPINECRAWLER).ready.exists:
            return
        actions = []
        s = self.potential_scout_units()
        if s.exists:
            scout = s.random
            self.scout_units.add(scout.tag)
            locs = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            locs.reverse()
            for i, p in enumerate(locs):
                if not self.is_visible(p):
                    actions.append(scout.move(p, queue=i > 0))
            await self.do_actions(actions)
            self.time_table["scout_expansions"] = self.time

    async def scout_watchtower(self):
        if self.townhalls.amount <= 2 and not self.units(UnitTypeId.SPINECRAWLER).ready.exists:
            return
        if self.state.units(UnitTypeId.XELNAGATOWER).amount > 0:
            for x in self.state.units(UnitTypeId.XELNAGATOWER):
                x: Unit = x
                s = self.potential_scout_units()
                scouts = self.units.of_type({UnitTypeId.ZERGLING, UnitTypeId.OVERLORD})
                if scouts.exists and scouts.closest_distance_to(x.position) > 2 and s.exists:
                    scout = s.random
                    self.scout_units.add(scout.tag)
                    await self.do_actions([
                        scout.move(x.position),
                        scout.hold_position(queue=True)
                    ])
                    self.time_table["scout_watchtower"] = self.time

    async def make_overseer(self):
        if self.units(UnitTypeId.LAIR).exists and \
                self.count_unit(UnitTypeId.OVERSEER) == 0 and \
                self.can_afford_or_change_production(UnitTypeId.OVERSEER):
            await self.do(self.units(UnitTypeId.OVERLORD).random(AbilityId.MORPH_OVERSEER))

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.vespene - self.minerals > 100:
            return False
        if self.already_pending(UnitTypeId.EXTRACTOR, all_units=True) > 0:
            return False
        if not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if self.townhalls.ready.amount < 2:
            return False
        if self.minerals - self.vespene > 500:
            return True
        if self.townhalls.amount < 3:
            return self.units(UnitTypeId.EXTRACTOR).amount == 0
        if self.townhalls.amount < 5:
            return self.units(UnitTypeId.EXTRACTOR).amount < self.townhalls.ready.amount * 2 - 2
        else:
            return self.units(UnitTypeId.EXTRACTOR).amount < self.townhalls.ready.amount * 2

    def should_expand(self):
        if self.already_pending(UnitTypeId.HATCHERY) > 0:
            return False
        if self.townhalls.amount == 1 and self.supply_used < 14:
            return False
        if not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if not (self.units(UnitTypeId.ROACHWARREN).exists or self.units(UnitTypeId.BANELINGNEST).exists):
            return self.townhalls.amount <= 1
        full_workers = True
        total_ideal_harvesters = 0
        for t in self.townhalls.ready:
            t: Unit = t
            full_workers = full_workers and t.surplus_harvesters >= 0
            total_ideal_harvesters += t.ideal_harvesters
        return full_workers and total_ideal_harvesters < 16 * 4

    def enemy_unit_history_count(self, unit_type: UnitTypeId) -> int:
        if unit_type not in self.enemy_unit_history:
            return 0
        return len(self.enemy_unit_history[unit_type])

    def is_location_safe(self, p: Point2):
        return not self.known_enemy_structures.of_type(
            {UnitTypeId.PHOTONCANNON, UnitTypeId.SPINECRAWLER, UnitTypeId.BUNKER}).closer_than(7, p).exists

    def target_position(self, unit: Unit) -> Optional[Point2]:
        tag_or_pos = unit.order_target
        if isinstance(tag_or_pos, int):
            unit = self.state.units.find_by_tag(tag_or_pos)
            target = unit.position if unit else None
        elif isinstance(tag_or_pos, Point2):
            target = tag_or_pos
        else:
            target = None
        return target

    async def defend_early_rush(self) -> bool:
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures. \
            of_type({UnitTypeId.BARRACKS}).closer_than(half_size, self.start_location)
        enemy_units = self.alive_enemy_units().exclude_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        enemy_drones = self.alive_enemy_units().of_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        townhall_to_defend = self.townhalls.ready.furthest_to(self.start_location)
        early_enemy_unit_count = self.enemy_unit_history_count(UnitTypeId.ZERGLING) + self.enemy_unit_history_count(
            UnitTypeId.MARINE) + self.enemy_unit_history_count(UnitTypeId.ZEALOT) + self.enemy_unit_history_count(
            UnitTypeId.BANELING) + self.enemy_unit_history_count(UnitTypeId.REAPER)
        # build spinecrawlers
        if 1 < self.townhalls.ready.amount < 3 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready and \
                self.count_unit(UnitTypeId.SPINECRAWLER) <= min(early_enemy_unit_count / 6, 2):
            await self.build(UnitTypeId.SPINECRAWLER,
                             near=townhall_to_defend.position.towards(self.game_info.map_center, 4),
                             random_alternative=False)
        if 0 < self.townhalls.ready.amount < 3 and (
                proxy_barracks.exists or
                enemy_units.amount > min(self.count_unit(UnitTypeId.ZERGLING), 5) or
                enemy_drones.amount > 5):
            await self.do(self.townhalls.ready.closest_to(self.start_location)(AbilityId.RALLY_HATCHERY_UNITS,
                                                                               townhall_to_defend.position))

            if self.units(UnitTypeId.ROACHWARREN).ready.exists:
                await self.train(UnitTypeId.ROACH)

            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if UpgradeId.ZERGLINGMOVEMENTSPEED in abilities:
                    await self.do(sp.first(UpgradeId.ZERGLINGMOVEMENTSPEED))
                await self.train(UnitTypeId.ZERGLING)
                if self.count_unit(UnitTypeId.SPINECRAWLER) <= 2:
                    await self.build(UnitTypeId.SPINECRAWLER,
                                     near=townhall_to_defend.position.towards(self.game_info.map_center, 4),
                                     random_alternative=False)
            else:
                await self.train(UnitTypeId.DRONE)

            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.workers.closer_than(2.5, a):
                    await self.do(w.gather(self.state.mineral_field.closest_to(w)))

            for d in self.units(UnitTypeId.DRONE).idle:
                await self.do(d.gather(self.state.mineral_field.closest_to(d)))

            forces = self.units.of_type({UnitTypeId.ROACH, UnitTypeId.ZERGLING})

            if forces.idle.amount > 40 and forces.idle.amount > self.alive_enemy_units().amount:
                for f in forces.idle.closer_than(half_size, self.start_location):
                    await self.do(f.attack(self.select_target()))

            if forces.idle.amount > 10:
                for f in forces.idle.random_group_of(
                        10 - min(forces.closer_than(half_size, self.enemy_start_locations[0]).amount, 10)):
                    f: Unit = f
                    if self.enemy_expansions.exists:
                        await self.do(f.move(self.enemy_expansions.closest_to(self.enemy_start_locations[0])))
                    else:
                        await self.do(f.move(self.enemy_start_locations[0]))

            for t in self.townhalls.ready:
                t: Unit = t
                await self.inject_larva(t)

            return True

        return False

    async def defend_cannon_rush(self):
        half_size = self.start_location.distance_to(self.game_info.map_center)
        cannons = self.known_enemy_structures.of_type({
            UnitTypeId.PYLON,
            UnitTypeId.PHOTONCANNON
        }).closer_than(20, self.start_location)
        if cannons.exists:
            # production queue
            if self.units(UnitTypeId.ROACHWARREN).ready.exists:
                await self.train(UnitTypeId.ROACH)

            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if UpgradeId.ZERGLINGMOVEMENTSPEED in abilities:
                    await self.do(sp.first(UpgradeId.ZERGLINGMOVEMENTSPEED))
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(7, self.start_location).amount <= 1:
                    await self.build(
                        UnitTypeId.SPINECRAWLER,
                        self.state.mineral_field.closest_to(self.start_location).position,
                        random_alternative=False
                    )
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(3, sp.first.position).amount <= 0:
                    p: Point2 = sp.first.position.random_on_distance(3)
                    if self.is_location_safe(p):
                        await self.build(
                            UnitTypeId.SPINECRAWLER,
                            sp.first.position,
                            random_alternative=False
                        )

                await self.train(UnitTypeId.ZERGLING)

            if self.should_build(UnitTypeId.SPAWNINGPOOL):
                p: Point2 = self.townhalls.furthest_to(self.start_location).position.random_on_distance(3)
                if self.is_location_safe(p):
                    await self.build(
                        UnitTypeId.SPINECRAWLER,
                        sp.first.position,
                        random_alternative=False
                    )

            # stop gathering gas
            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.workers.closer_than(2.5, a):
                    await self.do(w.gather(self.state.mineral_field.closest_to(w)))

            for d in self.units(UnitTypeId.DRONE).idle:
                await self.do(d.gather(self.state.mineral_field.closest_to(d)))

            forces = self.units.of_type({UnitTypeId.ROACH, UnitTypeId.ZERGLING})

            if forces.idle.amount > 40:
                for f in forces.closer_than(half_size, self.start_location):
                    await self.do(f.attack(self.select_target()))

            if forces.idle.amount > 10:
                for f in forces.idle.random_group_of(
                        10 - min(forces.closer_than(half_size, self.enemy_start_locations[0]).amount, 10)):
                    f: Unit = f
                    if self.enemy_expansions.exists:
                        await self.do(f.move(self.enemy_expansions.closest_to(self.enemy_start_locations[0])))
                    else:
                        await self.do(f.move(self.enemy_start_locations[0]))

            for t in self.townhalls.ready:
                t: Unit = t
                await self.inject_larva(t)
                lv = self.units(UnitTypeId.LARVA).closer_than(10, t.position)
                if t.surplus_harvesters < 0 and lv.exists:
                    await self.do(lv.first.train(UnitTypeId.DRONE))
            return True
        return False
