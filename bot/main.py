import json
import logging
import math
import random
from pathlib import Path
from typing import List, Dict, Set, Union

import sc2
from sc2 import Race
from sc2.cache import property_cache_once_per_frame
from sc2.constants import *
from sc2.position import Point2, Rect
from sc2.unit import Unit, UnitOrder
from sc2.units import Units

logger = sc2.main.logger

HEALTH_PERCENT = 0.1


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        super().__init__()
        self.last_scout_time = 0
        self.scout_units = set()
        self.base_trade_units = set()
        self.resource_list: List[List] = None
        self.time_table = {}
        self.value_table={}
        self.units_health: Dict[int, Union[int, float]] = {}
        self.units_attacked: Units = None
        self.creep_queen_tag = 0
        self.far_corners: Set[Point2] = set()
        self.hq: Unit = None
        self.all_in = False
        self.enemy_unit_history: Dict[UnitTypeId, Set[int]] = {}
        self.enemy_forces: Dict[int, Unit] = {}
        self.enemy_forces_supply: float = 0
        self.enemy_air_forces_supply: float = 0
        self.enemy_forces_stat: Dict[UnitTypeId, int] = 0
        self.enemy_forces_distance: float = -1
        self.enemy_has_changed = False
        self.first_overlord_tag = 0
        self.second_overlord_tag = 0
        self.iteration = 0
        self.reached_full_supply = False
        self.expand_target: Point2 = None
        self.air_defense = set()
        self.last_extractor_time = 0

        # enemy stats
        self.enemy_expansions: Units = None

        self.rally_point: Point2 = None

        self.build_order = []
        self.production_order = []
        self.forces: Units = None

        self.actions = []

    def _prepare_first_step(self):
        sc2.BotAI._prepare_first_step(self)
        self.expansion_locations.keys()
        a: Rect = self.game_info.playable_area
        corners = {Point2((a.x, a.y)), Point2((a.x, a.height)), Point2((a.width, a.y)), Point2((a.width, a.height))}
        s = self.start_location.closest(corners)
        e = self.enemy_start_locations[0].closest(corners)
        self.far_corners = corners - {s, e}

    # ._type_data._proto
    # unit_id: 104
    # name: "Drone"
    # available: true
    # cargo_size: 1
    # attributes: Light
    # attributes: Biological
    # movement_speed: 2.8125
    # armor: 0.0
    # weapons
    # {
    #     type: Ground
    #     damage: 5.0
    #     attacks: 1
    #     range: 0.10009765625
    #     speed: 1.5
    # }
    # mineral_cost: 50
    # vespene_cost: 0
    # food_required: 1.0
    # ability_id: 1342
    # race: Zerg
    # build_time: 272.0
    # sight_range: 8.0

    async def on_unit_destroyed(self, unit_tag):
        if unit_tag in self.enemy_forces:
            self.enemy_has_changed = True
            del self.enemy_forces[unit_tag]

    async def on_step(self, iteration):

        if self.time_budget_available and self.time_budget_available < 0.05:
            return

        self.production_order = []
        self.actions = []
        # enemy info
        self.calc_enemy_info()
        self.iteration = iteration

        attack_units = self.units.of_type({
            UnitTypeId.ZERGLING,
            UnitTypeId.BANELING,
            UnitTypeId.HYDRALISK,
            UnitTypeId.ROACH,
            UnitTypeId.OVERSEER,
            UnitTypeId.INFESTOR,
            UnitTypeId.INFESTORTERRAN
        })

        for f in attack_units.tags_in(self.base_trade_units):
            f: Unit = f
            if f.is_idle:
                self.base_trade_units.discard(f.tag)

        self.forces = attack_units.tags_not_in(self.scout_units | self.base_trade_units)

        half_size = self.start_location.distance_to(self.game_info.map_center)

        await self.chat_if_changed("enemy_expansions_count", self.enemy_expansions_count)

        # if i don't even have a townhall
        # this has to be there because sometimes `self.townhalls` returns nothing even though there're clearly townhalls
        if not self.townhalls.exists:
            for unit in self.units(UnitTypeId.DRONE) | self.units(UnitTypeId.QUEEN) | self.forces:
                self.actions.append(unit.attack(self.enemy_start_locations[0]))
            await self.do_actions(self.actions)
            return
        else:
            self.hq = self.townhalls.closest_to(self.start_location)
            if "redshift" in self.game_info.map_name.lower():
                exps = self.townhalls.sorted_by_distance_to(self.game_info.map_center)
                if exps[0].position.x < 29 and exps.amount > 1:
                    self.rally_point: Point2 = exps[1].position.towards(self.game_info.map_center, 4)
                else:
                    self.rally_point: Point2 = exps[0].position.towards(self.game_info.map_center, 4)
            else:
                self.rally_point: Point2 = self.townhalls.closest_to(
                    self.game_info.map_center).position.towards(self.game_info.map_center, 4)

        is_terran = self.enemy_race == Race.Terran or \
                    (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Terran)

        is_zerg = self.enemy_race == Race.Zerg or \
                  (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Zerg)

        is_protoss = self.enemy_race == Race.Protoss or \
                     (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Protoss)

        if is_terran:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.BANELINGNEST,
                UnitTypeId.INFESTATIONPIT,
                UnitTypeId.EVOLUTIONCHAMBER,
                UnitTypeId.HYDRALISKDEN,
            ]
        elif is_zerg:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.ROACHWARREN,
                UnitTypeId.INFESTATIONPIT,
                UnitTypeId.EVOLUTIONCHAMBER,
                UnitTypeId.HYDRALISKDEN,
            ]
        else:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.INFESTATIONPIT,
                UnitTypeId.EVOLUTIONCHAMBER,
                UnitTypeId.HYDRALISKDEN,
            ]

        # supply_cap does not include overload that is being built
        est_supply_cap = (self.count_unit(UnitTypeId.OVERLORD)) * 8 + self.townhalls.ready.amount * 6
        est_supply_left = est_supply_cap - self.supply_used
        if self.units(UnitTypeId.OVERLORD).amount == 1 and \
                (self.townhalls.amount < 2 or not self.units(UnitTypeId.SPAWNINGPOOL).exists):
            build_overlord = False
        elif self.units(UnitTypeId.OVERLORD).amount <= 4:
            build_overlord = est_supply_left < 3
        else:
            build_overlord = est_supply_left < 9

        if build_overlord and est_supply_cap < 200:
            self.train(UnitTypeId.OVERLORD)

        # attacks
        if self.enemy_near_townhall.exists:
            if self.enemy_near_townhall.amount > self.forces.amount + self.count_spinecrawler() * 2:
                ws = self.workers.closer_than(20, self.enemy_near_townhall.first.position)
                n = min(ws.amount, round(self.enemy_near_townhall.amount * 1.5))
                if ws.filter(lambda w: w.is_attacking).amount < n:
                    self.actions.append(
                        ws.filter(lambda w: not w.is_attacking).random.attack(self.enemy_near_townhall.first.position))
            for unit in self.forces:
                unit: Unit = unit
                if unit.type_id == UnitTypeId.INFESTOR:
                    self.infestor_cast(unit)
                    continue
                if self.enemy_near_townhall.not_flying.amount <= 0 and not unit.can_attack_air:
                    self.move_and_attack(unit, self.attack_target)
                    continue
                # fight within spinecrawler
                sc = self.units(UnitTypeId.SPINECRAWLER)
                if not sc.exists or self.enemy_near_townhall.not_flying.amount <= 0 or \
                        sc.closest_distance_to(self.enemy_near_townhall.first) > 15:
                    self.move_and_attack(unit, self.enemy_near_townhall.first.position)
                    continue
                if sc.filter(lambda u: u.is_ready and u.is_attacking).exists or \
                        self.units_attacked.of_type(UnitTypeId.SPINECRAWLER).exists:
                    self.move_and_attack(unit, self.enemy_near_townhall.first.position)
                else:
                    self.actions.append(unit.move(sc.first.position.towards(self.start_location, 7)))
            if 0 < self.enemy_forces_distance < half_size:
                for unit in self.units(UnitTypeId.SWARMHOSTMP).ready:
                    abilities = (await self.get_available_abilities([unit]))[0]
                    if AbilityId.EFFECT_SPAWNLOCUSTS in abilities:
                        self.actions.append(
                            unit(AbilityId.EFFECT_SPAWNLOCUSTS, self.enemy_near_townhall.first.position))
        elif self.supply_used > 190 or self.surplus_forces > len(self.base_trade_units) * 0.5:
            for w in self.workers:
                if w.is_attacking:
                    self.actions.append(w.stop())
            for unit in self.forces:
                if unit.type_id == UnitTypeId.INFESTOR:
                    self.infestor_cast(unit)
                    self.actions.append(unit.move(
                        self.forces.closest_to(self.attack_target).position.towards(self.start_location, 5)
                    ))
                elif unit.type_id == UnitTypeId.OVERSEER:
                    self.actions.append(unit.move(self.forces.center))
                else:
                    self.move_and_attack(unit, self.attack_target)
        else:
            for w in self.workers:
                if w.is_attacking:
                    self.actions.append(w.stop())
            t = self.expand_target if self.expand_target is not None else self.rally_point
            for unit in self.forces.further_than(10, t):
                if unit.type_id == UnitTypeId.OVERSEER and has_order(unit, AbilityId.SPAWNCHANGELING_SPAWNCHANGELING):
                    continue
                self.actions.append(unit.move(t))
        swarmhost = self.units(UnitTypeId.SWARMHOSTMP).ready
        for s in swarmhost:
            s: Unit = s
            e: Units = self.known_enemy_units.closer_than(15, s.position)
            abilities = (await self.get_available_abilities([s]))[0]
            if AbilityId.EFFECT_SPAWNLOCUSTS in abilities:
                if count_supply(e.not_flying) > 5 or e.structure.exists:
                    self.actions.append(s(AbilityId.EFFECT_SPAWNLOCUSTS, e.random.position))
                else:
                    if self.enemy_expansions.exists:
                        closest_exp = self.enemy_expansions.closest_to(s.position)
                    else:
                        closest_exp = self.enemy_start_locations[0]
                    self.actions.append(s.move(closest_exp.position.towards(self.start_location, 20)))
                    self.actions.append(s(AbilityId.EFFECT_SPAWNLOCUSTS, closest_exp.position, queue=True))
            else:
                if e.exists:
                    self.actions.append(s.move(s.position.towards(self.rally_point, 15)))
                else:
                    self.actions.append(s.stop())

        # attack reactions
        for x in self.units_attacked:
            x: Unit = x
            workers_nearby = self.workers.closer_than(5, x.position).filter(lambda wk: not wk.is_attacking)
            enemy_nearby = self.visible_enemy_units.closer_than(10, x.position)
            if not enemy_nearby.exists:
                continue
            if x.type_id == UnitTypeId.DRONE:
                another_townhall = self.townhalls.further_than(25, x.position)
                if self.forces.amount > enemy_nearby.amount and \
                        another_townhall.exists and self.townhalls.ready.amount > 3:
                    self.actions.append(x.move(another_townhall.first.position))
                elif workers_nearby.amount > 2:
                    self.actions.append(x.attack(enemy_nearby.first))
                    for w in workers_nearby:
                        w: Unit = w
                        if not w.is_attacking:
                            self.actions.append(w.attack(enemy_nearby.first))
            elif x.is_structure:
                if x.build_progress < 1 and x.health_percentage < min(x.build_progress, HEALTH_PERCENT):
                    self.actions.append(x(AbilityId.CANCEL))
            elif x.type_id == UnitTypeId.SWARMHOSTMP:
                self.actions.append(x.move(self.rally_point))
            elif x.type_id == UnitTypeId.INFESTOR:
                self.infestor_cast(x)
            elif x.type_id == UnitTypeId.OVERSEER:
                self.actions.append(x(AbilityId.SPAWNCHANGELING_SPAWNCHANGELING))
            elif x.tag == self.first_overlord_tag:
                self.actions.append(x.move(x.position.towards(self.game_info.map_center, 10)))
            elif x.type_id == UnitTypeId.OVERLORD:
                self.actions.append(x.move(x.position.towards(self.start_location, 10)))

        overseers = self.units(UnitTypeId.OVERSEER)
        if overseers.exists:
            abilities: List[List[AbilityId]] = await self.get_available_abilities(overseers)
            for i, a in enumerate(abilities):
                if AbilityId.SPAWNCHANGELING_SPAWNCHANGELING in a:
                    u: Unit = overseers[i]
                    if u.distance_to(self.attack_target) > 25 and \
                            not has_order(u, AbilityId.SPAWNCHANGELING_SPAWNCHANGELING):
                        self.actions.append(
                            u.move(self.attack_target.towards_with_random_angle(self.game_info.map_center, 25)))
                    self.actions.append(u(AbilityId.SPAWNCHANGELING_SPAWNCHANGELING, queue=True))

        changelings = self.units(UnitTypeId.CHANGELING).idle
        if changelings.exists:
            locs = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            locs.reverse()
            for i, p in enumerate(locs):
                if not self.is_visible(p) and p.distance_to(self.enemy_start_locations[0]) < half_size:
                    self.actions.append(changelings.first.move(p, queue=i > 0))

        # counter timing attack
        if await self.defend_early_rush():
            await self.do_actions(self.actions)
            return

        # base trade
        if self.enemy_expansions.exists:
            p = self.enemy_expansions[0].position.closest(self.far_corners)
        else:
            p = self.enemy_start_locations[0].closest(self.far_corners)
        zs = self.units(UnitTypeId.ZERGLING).tags_not_in(self.base_trade_units | self.scout_units)
        if self.est_defense_surplus > 0 and zs.exists and self.townhalls.amount > 2 and \
                self.units(UnitTypeId.ZERGLING).tags_in(self.base_trade_units).amount < self.forces.amount and \
                self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 1:
            z = zs.random
            self.base_trade_units.add(z.tag)
            self.actions.extend([
                z.move(p),
                z.patrol(p.towards(self.game_info.map_center, 10), queue=True),
            ])

        if self.should_base_trade():
            for f in self.units(UnitTypeId.ZERGLING).tags_in(self.base_trade_units):
                if f.is_patrolling:
                    self.actions.append(f.attack(self.enemy_start_locations[0]))
        else:
            for f in self.units(UnitTypeId.ZERGLING).tags_in(self.base_trade_units):
                if f.is_attacking:
                    self.actions.extend([
                        f.move(p),
                        f.patrol(p.towards(self.game_info.map_center, 10), queue=True),
                    ])

        # build spinecrawlers
        if self.count_spinecrawler() < 1 and \
                self.townhalls.ready.amount > 1 and \
                self.can_afford_or_change_production(UnitTypeId.SPINECRAWLER):
            await self.build_spine_crawler()

        for s in self.units(UnitTypeId.SPINECRAWLER).ready.idle:
            if s.distance_to(self.rally_point) > 10 and self.has_creep(self.rally_point):
                self.actions.append(s(AbilityId.SPINECRAWLERUPROOT_SPINECRAWLERUPROOT))

        for s in self.units(UnitTypeId.SPINECRAWLERUPROOTED).ready.idle:
            t = await self.find_placement(
                UnitTypeId.SPINECRAWLER, self.rally_point.towards(self.game_info.map_center, 3), 5, False, 1)
            if t is not None:
                self.actions.append(s(AbilityId.SPINECRAWLERROOT_SPINECRAWLERROOT, t, queue=True))

        for s in self.units(UnitTypeId.SPORECRAWLER):
            if s.tag not in self.air_defense and s.distance_to(self.rally_point) <= 10:
                self.air_defense.add(s.tag)
            if s.is_idle and s.is_ready and \
                    s.tag in self.air_defense and s.distance_to(self.rally_point) > 10 and \
                    self.has_creep(self.rally_point):
                self.actions.append(s(AbilityId.SPORECRAWLERUPROOT_SPORECRAWLERUPROOT))

        af = len(self.air_defense) * 4 + self.forces.of_type({UnitTypeId.HYDRALISK}).amount * 2
        if af < self.enemy_air_forces_supply and self.workers.amount >= 32:
            await self.build(UnitTypeId.SPORECRAWLER,
                             self.rally_point.towards(self.game_info.map_center, 2),
                             max_distance=4,
                             placement_step=1)

        for s in self.units(UnitTypeId.SPORECRAWLERUPROOTED).ready.idle:
            t = await self.find_placement(
                UnitTypeId.SPORECRAWLER, self.rally_point.towards(self.game_info.map_center, 3), 5, False, 1)
            if t is not None:
                self.actions.append(s(AbilityId.SPORECRAWLERROOT_SPORECRAWLERROOT, t, queue=True))

        for q in self.units(UnitTypeId.QUEEN):
            es: Units = self.visible_enemy_units.filter(lambda u: u.target_in_range(q))
            if es.exists and self.townhalls.closest_distance_to(q) < 10:
                self.move_and_attack(q, es.closest_to(q).position)

        # economy
        for t in self.townhalls.ready:
            t: Unit = t
            queen_nearby = await self.dist_workers_and_inject_larva(t)
            if self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag) is None and queen_nearby.amount > 1:
                self.creep_queen_tag = queen_nearby[1].tag
            if self.workers.amount >= 32:
                if not self.units(UnitTypeId.SPORECRAWLER).closer_than(10, t.position).exists and \
                        self.already_pending(UnitTypeId.SPORECRAWLER) == 0:
                    await self.build(UnitTypeId.SPORECRAWLER,
                                     near=self.state.mineral_field.closer_than(10, t.position).center,
                                     random_alternative=False)

        need_workers = self.count_unit(UnitTypeId.DRONE) < self.townhalls.amount * 16 + self.units(
            UnitTypeId.EXTRACTOR).amount * 3
        if need_workers and self.count_unit(UnitTypeId.DRONE) < 76 and self.should_produce_worker():
            self.production_order.append(UnitTypeId.DRONE)

        # production queue
        # infestor
        # if self.units(UnitTypeId.INFESTATIONPIT).ready.exists and self.count_unit(UnitTypeId.INFESTOR) < 3:
        #     self.production_order.append(UnitTypeId.INFESTOR)

        if self.units(UnitTypeId.HYDRALISKDEN).ready.exists and self.can_afford(UnitTypeId.HYDRALISK):
            self.production_order.append(UnitTypeId.HYDRALISK)
        elif self.units(UnitTypeId.ROACHWARREN).ready.exists:
            self.production_order.append(UnitTypeId.ROACH)

        # swarm host
        if self.units(UnitTypeId.INFESTATIONPIT).ready.exists and self.count_unit(UnitTypeId.SWARMHOSTMP) < 10:
            if UnitTypeId.DRONE in self.production_order and \
                    self.really_need_workers and \
                    self.count_unit(UnitTypeId.DRONE) < 16 * 3:
                self.production_order = [UnitTypeId.SWARMHOSTMP, UnitTypeId.DRONE]
            else:
                self.production_order = [UnitTypeId.SWARMHOSTMP]

        # zerglings
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists:
            if self.count_unit(UnitTypeId.ZERGLING) < 6 + self.state.units(UnitTypeId.XELNAGATOWER).amount:
                self.production_order = [UnitTypeId.ZERGLING]
            elif self.townhalls.ready.amount == 2 and \
                    self.enemy_expansions_count < 2 and \
                    self.count_unit(UnitTypeId.ZERGLING) < 20:
                self.production_order.insert(0, UnitTypeId.ZERGLING)
            elif self.units.of_type({
                UnitTypeId.ROACHWARREN,
                UnitTypeId.HYDRALISKDEN,
                UnitTypeId.INFESTATIONPIT
            }).ready.exists and self.minerals - self.vespene < 100:
                pass
            else:
                self.production_order.append(UnitTypeId.ZERGLING)

        # banelings
        if self.units(UnitTypeId.BANELINGNEST).ready.exists and self.units(UnitTypeId.ZERGLING).exists:
            b = self.count_enemy_unit(UnitTypeId.MARINE) * 0.35
            if self.count_unit(UnitTypeId.BANELING) < b:
                self.production_order.append(UnitTypeId.BANELING)

        # lair upgrade
        if not self.units(UnitTypeId.LAIR).exists and \
                not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.LAIR, all_units=True) == 0 and \
                self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) > 0 and \
                self.can_afford_or_change_production(UnitTypeId.LAIR):
            self.actions.append(self.hq.build(UnitTypeId.LAIR))

        # hive upgrade
        if not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.HIVE, all_units=True) == 0 and \
                self.units(UnitTypeId.INFESTATIONPIT).ready.exists and \
                self.supply_used > 190 and \
                self.can_afford_or_change_production(UnitTypeId.HIVE):
            self.actions.append(self.hq.build(UnitTypeId.HIVE))

        await self.call_every(self.scout_expansions, 2 * 60)
        await self.call_every(self.scout_watchtower, 60)
        await self.call_every(self.chat_resource, 30)
        await self.fill_creep_tumor()
        await self.make_overseer()

        # expansion
        if self.should_expand() and self.can_afford_or_change_production(UnitTypeId.HATCHERY):
            if self.townhalls.ready.amount == 3 and random.random() > 0.5:
                fc = self.start_location.sort_by_distance(self.far_corners)
                exps = fc[0].sort_by_distance(self.expansion_locations.keys())
            else:
                exps = self.start_location.sort_by_distance(self.expansion_locations.keys())
            for p in exps:
                if p.distance_to(self.start_location) <= p.distance_to(self.enemy_start_locations[0]) and \
                        await self.can_place(UnitTypeId.HATCHERY, p):
                    self.expand_target = p
                    await self.expand_now(None, 2, p)
                    return

        # first overlord scout
        if self.units(UnitTypeId.OVERLORD).amount == 1:
            o: Unit = self.units(UnitTypeId.OVERLORD).first
            self.first_overlord_tag = o.tag
            exps = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            self.actions.extend([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 18)),
                o.move(exps[1].towards(self.game_info.map_center, 5), queue=True),
            ])

        # second overlord scout
        o: Units = self.units.tags_in({self.second_overlord_tag})
        if not o.exists:
            o: Units = self.units(UnitTypeId.OVERLORD).tags_not_in({self.first_overlord_tag})
            if o.exists:
                self.second_overlord_tag = o.first.tag
        elif o.first.is_idle and o.first.health_percentage >= HEALTH_PERCENT:
            self.actions.append(o.first.move(self.rally_point.towards(self.game_info.map_center, 5), queue=True))
            self.actions.append(o.first.move(self.rally_point.towards(self.game_info.map_center, 25), queue=True))

        # extractor and gas gathering
        if self.should_build_extractor() and self.time - self.last_extractor_time > 5:
            self.last_extractor_time = self.time
            drone = self.empty_workers.random
            target = self.state.vespene_geyser.filter(lambda u: not u.is_mine).closest_to(drone.position)
            if self.townhalls.ready.closest_distance_to(target.position) < 10:
                self.actions.append(drone.build(UnitTypeId.EXTRACTOR, target))

        for a in self.units(UnitTypeId.EXTRACTOR).ready:
            a: Unit = a
            if self.vespene - self.minerals > 100:
                w: Units = self.empty_workers.closer_than(2.5, a)
                t: Unit = self.townhalls.closest_to(a.position)
                if t.surplus_harvesters < 0 and t.distance_to(a) < 10 and w.exists and w.first.order_target == a.tag:
                    self.actions.append(w.first.gather(self.state.mineral_field.closest_to(w.first)))
            elif a.assigned_harvesters < a.ideal_harvesters:
                w: Units = self.empty_workers.closer_than(20, a)
                if w.exists:
                    self.actions.append(w.random.gather(a))
                    continue
            elif a.assigned_harvesters > a.ideal_harvesters:
                for w in self.empty_workers.closer_than(2.5, a):
                    if w.order_target == a.tag:
                        self.actions.append(w.gather(self.state.mineral_field.closest_to(w)))

        # overlord speed
        if self.units(UnitTypeId.LAIR).ready.exists and \
                self.already_pending_upgrade(UpgradeId.OVERLORDSPEED) == 0 and \
                self.can_afford_or_change_production(UpgradeId.OVERLORDSPEED):
            self.actions.append(self.hq.research(UpgradeId.OVERLORDSPEED))

        # burrow
        # if self.supply_used > 100 and self.already_pending_upgrade(UpgradeId.BURROW) == 0 and \
        #         self.can_afford_or_change_production(UpgradeId.BURROW):
        #     self.actions.append(self.hq.research(UpgradeId.BURROW))

        # drone
        self.drone_gather()

        await self.build_building()
        await self.upgrade_building()
        await self.produce_unit()
        await self.do_actions(self.actions)

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
                self.townhalls.amount >= 2 and \
                self.count_unit(UnitTypeId.QUEEN) <= self.townhalls.ready.amount and \
                self.townhalls.ready.idle.exists and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN):
            self.actions.append(self.townhalls.ready.idle.furthest_to(self.start_location).train(UnitTypeId.QUEEN))
        if creep_queen is not None and creep_queen.is_idle:
            abilities = await self.get_available_abilities(creep_queen)
            if AbilityId.BUILD_CREEPTUMOR_QUEEN in abilities:
                t = self.townhalls.ready.furthest_to(self.start_location).position.random_on_distance(10)
                if creep_tumors.exists:
                    ct = creep_tumors.furthest_to(self.start_location).position.random_on_distance(10)
                    if ct.distance2_to(self.start_location) > t.distance2_to(self.start_location):
                        t = ct
                if self.has_creep(t) and self.can_place_creep_tumor(t):
                    self.actions.append(creep_queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, t))
        available_creep_tumors = self.units(UnitTypeId.CREEPTUMORBURROWED)
        if available_creep_tumors.exists:
            abilities: List[List[AbilityId]] = await self.get_available_abilities(available_creep_tumors)
            for i, a in enumerate(abilities):
                if AbilityId.BUILD_CREEPTUMOR_TUMOR in a:
                    u: Unit = available_creep_tumors[i]
                    t = self.calc_creep_tumor_position(u)
                    if t is not None:
                        self.actions.append(u(AbilityId.BUILD_CREEPTUMOR_TUMOR, t))

    @property_cache_once_per_frame
    def enemy_expansions_count(self) -> int:
        if self.enemy_expansions.amount == 0:
            return 1
        if self.enemy_expansions.closest_distance_to(self.enemy_start_locations[0]) > 5:
            return self.enemy_expansions.amount + 1
        else:
            return self.enemy_expansions.amount

    @property_cache_once_per_frame
    def really_need_workers(self) -> bool:
        return self.count_unit(UnitTypeId.DRONE) < self.townhalls.ready.amount * 16 + self.units(
            UnitTypeId.EXTRACTOR).ready.amount * 3

    def should_base_trade(self):
        half_size = self.start_location.distance_to(self.game_info.map_center)
        if self.enemy_forces_distance < half_size:
            return True
        if count_supply(self.enemy_near_townhall) > 5:
            return True
        if self.supply_used > 190:
            return True
        if self.known_enemy_units.exists and self.forces.exists and \
                self.known_enemy_units.closest_distance_to(self.forces.center) < 15:
            return True

    def drone_gather(self):
        for d in self.units(UnitTypeId.DRONE).idle:
            d: Unit = d
            if self.need_worker_mineral is not None:
                self.actions.append(d.gather(self.need_worker_mineral))
            else:
                self.actions.append(
                    d.gather(self.state.mineral_field.closest_to(self.townhalls.closest_to(d.position))))

    async def dist_workers_and_inject_larva(self, townhall: Unit) -> Units:
        excess_worker = self.empty_workers.closer_than(10, townhall.position)
        m = self.need_worker_mineral
        if townhall.assigned_harvesters > townhall.ideal_harvesters and excess_worker.exists and m is not None:
            self.actions.append(excess_worker.random.gather(m))
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                townhall.is_ready and townhall.noqueue and \
                self.units(UnitTypeId.QUEEN).closer_than(10, townhall.position).amount == 0 and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN):
            self.actions.append(townhall.train(UnitTypeId.QUEEN))
        queen_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(10, townhall.position)
        if queen_nearby.tags_not_in({self.creep_queen_tag}).amount > 0:
            queen = queen_nearby.tags_not_in({self.creep_queen_tag}).first
            abilities = await self.get_available_abilities(queen)
            if AbilityId.EFFECT_INJECTLARVA in abilities:
                self.actions.append(queen(AbilityId.EFFECT_INJECTLARVA, townhall))
        return queen_nearby

    def should_produce_worker(self):
        if self.townhalls.ready.amount == 1 and self.count_unit(UnitTypeId.DRONE) < 14:
            return True
        if self.count_unit(UnitTypeId.ZERGLING) < 6 and self.count_unit(UnitTypeId.DRONE) >= 14:
            return False
        if not self.units.of_type({UnitTypeId.HYDRALISKDEN, UnitTypeId.ROACHWARREN}).exists:
            return True
        return self.est_defense_surplus >= 0 or self.really_need_workers

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

    def move_and_attack(self, u: Unit, t: Point2):
        banelings: Units = self.visible_enemy_units.of_type({UnitTypeId.BANELING}).closer_than(4, u.position)
        if banelings.exists:
            b = banelings.closest_to(u.position)
            self.actions.append(u.move(backwards(u.position, b.position, 4)))
            if u.ground_range > 1:
                self.actions.append(u.attack(b, queue=True))
            return
        if u.type_id == UnitTypeId.BANELING:
            ec: Units = self.visible_enemy_units.closer_than(10, u.position)
            if not ec.exists or ec.of_type({UnitTypeId.MARINE}).amount > 2 or u.distance_to(self.rally_point) < 10:
                self.actions.append(u.attack(t))
            else:
                self.actions.append(u.move(self.rally_point))
            return
        if u.type_id == UnitTypeId.ZERGLING:
            front_line: Units = self.forces.of_type({UnitTypeId.ROACH, UnitTypeId.HYDRALISK, UnitTypeId.BANELING})
            if not self.known_enemy_units.closer_than(10, u.position).exists and \
                    front_line.exists and front_line.closest_distance_to(t) + 5 > u.distance_to(t):
                self.actions.append(u.stop())
            else:
                self.actions.append(u.attack(t))
            return
        enemy: Units = self.visible_enemy_units.filter(lambda e: e.target_in_range(u))
        if enemy.exists and u.weapon_cooldown > 0:
            c = enemy.closest_to(u.position)
            self.actions.extend([
                u.move(backwards(u.position, c.position, u.movement_speed * u.weapon_cooldown)),
                u.attack(t, queue=True)
            ])
        else:
            self.actions.append(u.attack(t))

    @property_cache_once_per_frame
    def empty_workers(self) -> Units:
        def has_no_resource(u: Unit):
            return not u.is_carrying_minerals and not u.is_carrying_vespene

        return self.workers.filter(has_no_resource)

    def infestor_cast(self, unit: Unit):
        e: Units = self.visible_enemy_units.closer_than(10, unit.position)
        if unit.energy >= 75 and e.amount > 5 and self.units.closer_than(10, unit.position).amount > 5:
            self.actions.append(unit(AbilityId.FUNGALGROWTH_FUNGALGROWTH, e.random.position))
        elif unit.energy >= 25 and e.amount > 5:
            self.actions.append(unit(AbilityId.INFESTEDTERRANS_INFESTEDTERRANS, e.random.position))
        elif unit.energy < 25:
            self.actions.append(unit.move(self.rally_point))

    def count_enemy_unit(self, u: UnitTypeId) -> 0:
        if u in self.enemy_forces_stat:
            return self.enemy_forces_stat[u]
        else:
            return 0

    def can_place_creep_tumor(self, t: Point2) -> bool:
        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        exp_points = list(self.expansion_locations.keys())
        return not creep_tumors.closer_than(10, t).exists and t.position.distance_to_closest(exp_points) > 5

    @property_cache_once_per_frame
    def est_surplus_forces(self):
        forces_supply = self.supply_used - self.count_unit(UnitTypeId.DRONE) - self.count_unit(UnitTypeId.QUEEN) * 2
        return forces_supply - self.enemy_forces_supply

    @property_cache_once_per_frame
    def surplus_forces(self):
        forces_supply = self.units.of_type({UnitTypeId.ZERGLING, UnitTypeId.BANELING}).amount * 0.5 + \
                        self.units.of_type({UnitTypeId.ROACH, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR}).amount * 2 + \
                        self.units.of_type({UnitTypeId.SWARMHOSTMP}).amount * 3
        return forces_supply - self.enemy_forces_supply

    async def upgrade_building(self):
        for b in self.build_order:
            if b == UnitTypeId.INFESTATIONPIT:
                continue
            u = self.units(b).ready.idle
            if u.exists:
                abilities = await self.get_available_abilities(u.first, ignore_resource_requirements=True)
                if AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL1 in abilities:
                    abilities = [AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL1]
                if AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL2 in abilities:
                    abilities = [AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL2]
                if AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL3 in abilities:
                    abilities = [AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL3]
                if AbilityId.RESEARCH_GLIALREGENERATION in abilities:
                    abilities.remove(AbilityId.RESEARCH_GLIALREGENERATION)
                if AbilityId.RESEARCH_MUSCULARAUGMENTS in abilities and \
                        self.count_unit(UnitTypeId.HYDRALISK) < 10 and \
                        self.supply_used < 190:
                    abilities.remove(AbilityId.RESEARCH_MUSCULARAUGMENTS)
                if len(abilities) > 0 and self.can_afford_or_change_production(abilities[0]):
                    self.actions.append(u.first(abilities[0]))

    async def build_building(self):
        if self.townhalls.amount < 2 or self.supply_used < 14:
            return
        for i, b in enumerate(self.build_order):
            for t in self.townhalls.sorted_by_distance_to(self.start_location):
                t: Unit = t
                p = self.find_building_location(t)
                if (i == 0 or self.units(self.build_order[i - 1]).exists) and self.should_build(
                        b) and self.is_location_safe(p):
                    if b == UnitTypeId.ROACHWARREN and self.count_unit(UnitTypeId.DRONE) < 16 * 2:
                        return
                    if b == UnitTypeId.BANELINGNEST and self.count_unit(UnitTypeId.DRONE) < 16 * 2:
                        return
                    if b == UnitTypeId.INFESTATIONPIT and not self.units(UnitTypeId.LAIR).ready.exists:
                        return
                    if b == UnitTypeId.HYDRALISKDEN and self.count_unit(UnitTypeId.SWARMHOSTMP) < 5:
                        return
                    await self.build(b, near=p)
                    return

        if self.count_unit(UnitTypeId.EVOLUTIONCHAMBER) == 1 and self.supply_used > 100:
            await self.build(UnitTypeId.EVOLUTIONCHAMBER, near=self.find_building_location(self.hq.position))

    def find_building_location(self, t: Unit) -> Point2:
        m = self.state.mineral_field.closer_than(10, t.position)
        if m.exists:
            return backwards(t.position, m.center, 10)
        else:
            return t.position.towards_with_random_angle(self.game_info.map_center, 10)

    def should_build(self, b):
        return not self.units(b).exists and self.already_pending(b) == 0 and self.can_afford(b)

    def count_unit(self, unit_type: UnitTypeId) -> int:
        factor = 2 if unit_type == UnitTypeId.ZERGLING else 1
        return self.units(unit_type).amount + factor * self.already_pending(unit_type, all_units=True)

    @property_cache_once_per_frame
    def attack_target(self):
        if self.known_enemy_structures.exists:
            target = self.known_enemy_structures.furthest_to(self.enemy_start_locations[0])
            return target.position
        return self.enemy_start_locations[0]

    @property_cache_once_per_frame
    def enemy_near_townhall(self) -> Units:
        result = set()
        for t in self.townhalls:
            enemy: Units = self.visible_enemy_units.closer_than(20, t.position)
            for e in enemy:
                e: Unit = e
                result.add(e.tag)
        return self.visible_enemy_units.tags_in(result).sorted_by_distance_to(self.start_location)

    @property_cache_once_per_frame
    def visible_enemy_units(self) -> Units:
        def alive_and_can_attack(u: Unit) -> bool:
            return u.health > 0 and not u.is_structure and (u._type_data._proto.food_required)

        return self.known_enemy_units.filter(alive_and_can_attack)

    def calc_enemy_info(self):

        if self.expand_target is not None:
            if self.townhalls.closer_than(10, self.expand_target).exists:
                self.expand_target = None

        self.enemy_expansions = self.known_enemy_structures.of_type({
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.PLANETARYFORTRESS
        }).sorted_by_distance_to(self.start_location)

        for e in self.known_enemy_units:
            e: Unit = e
            if e.type_id not in self.enemy_unit_history:
                self.enemy_unit_history[e.type_id] = set()
            if e.health > 0 and not e.is_structure and e.type_id not in {UnitTypeId.DRONE,
                                                                         UnitTypeId.SCV,
                                                                         UnitTypeId.PROBE}:
                self.enemy_forces[e.tag] = e
                self.enemy_has_changed = True
            self.enemy_unit_history[e.type_id].add(e.tag)

        self.enemy_forces_supply = 0
        self.enemy_air_forces_supply = 0
        self.enemy_forces_stat = {}
        distance = 0
        for k, v in self.enemy_forces.items():
            self.enemy_forces_supply += v._type_data._proto.food_required
            if v.is_flying:
                self.enemy_air_forces_supply += v._type_data._proto.food_required
            if v.type_id in self.enemy_forces_stat:
                self.enemy_forces_stat[v.type_id] += 1
            else:
                self.enemy_forces_stat[v.type_id] = 1
            if self.enemy_has_changed:
                distance += v.distance_to(self.start_location)
        if distance > 0 and len(self.enemy_forces) > 0:
            self.enemy_forces_distance = distance / len(self.enemy_forces)

        self.enemy_has_changed = False

        def not_full_health(u: Unit) -> bool:
            return u.health < u.health_max

        units_attacked = set()
        for w in self.units.filter(not_full_health):
            w: Unit = w
            if (w.tag in self.units_health and w.health < self.units_health[w.tag]) or w.tag not in self.units_health:
                units_attacked.add(w.tag)
            self.units_health[w.tag] = w.health

        self.units_attacked = self.units.tags_in(units_attacked)
        logger.info(
            "surplus=%s est_surplus=%s dist=%s",
            self.surplus_forces,
            self.est_surplus_forces,
            self.enemy_forces_distance
        )

    async def produce_unit(self):
        if self.supply_left == 0:
            return
        for u in self.production_order:
            if u == UnitTypeId.BANELING:
                z = self.forces.of_type({UnitTypeId.ZERGLING})
                if z.exists:
                    t = z.closest_to(self.start_location)
                    if not self.visible_enemy_units.closer_than(10, t.position).exists:
                        self.actions.append(t(AbilityId.MORPHZERGLINGTOBANELING_BANELING))
            else:
                self.train(u)

    def train(self, u):
        lv = self.units(UnitTypeId.LARVA)
        if lv.exists and self.can_afford(u):
            self.actions.append(lv.random.train(u))

    async def call_every(self, func, seconds):
        if func.__name__ not in self.time_table:
            self.time_table[func.__name__] = 0
        if self.time - self.time_table[func.__name__] > seconds:
            await func()

    async def chat_if_changed(self, key, value):
        if key not in self.value_table:
            await self.chat_send(f"{self.time_formatted} {key}: None -> {value}")
            self.value_table[key] = value
        elif self.value_table[key] != value:
            await self.chat_send(f"{self.time_formatted} {key}: {self.value_table[key]} -> {value}")
            self.value_table[key] = value


    def can_afford_or_change_production(self, u):

        def remove_if_exists(l, i):
            if i in l:
                l.remove(i)

        can_afford = self.can_afford(u)
        if not can_afford.can_afford_minerals:
            if UnitTypeId.DRONE in self.production_order:
                self.production_order = [UnitTypeId.DRONE]
            else:
                self.production_order = []
        if not can_afford.can_afford_vespene:
            remove_if_exists(self.production_order, UnitTypeId.ROACH)
            remove_if_exists(self.production_order, UnitTypeId.HYDRALISK)
            remove_if_exists(self.production_order, UnitTypeId.MUTALISK)
            remove_if_exists(self.production_order, UnitTypeId.INFESTOR)
            remove_if_exists(self.production_order, UnitTypeId.SWARMHOSTMP)
        return can_afford

    def potential_scout_units(self):
        if self.supply_used > 190 and self.already_pending(UpgradeId.OVERLORDSPEED) == 1:
            scouts = self.units(UnitTypeId.OVERLORD).tags_not_in(self.scout_units)
        else:
            scouts = self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units)
        return scouts

    async def chat_resource(self):
        s = self.state.score
        await self.chat_send(
            f"{self.time_formatted} M = {s.lost_minerals_army} V = {s.lost_vespene_army} "
            f"EM = {s.killed_minerals_army} EV = {s.killed_vespene_army}"
        )
        self.time_table["chat_resource"] = self.time

    async def scout_expansions(self):
        if self.townhalls.amount <= 2 and (
                not self.units(UnitTypeId.SPINECRAWLER).exists or
                self.units(UnitTypeId.SPINECRAWLER).first.build_progress < 0.5
        ):
            return
        s = self.potential_scout_units()
        if s.exists:
            scout = s.random
            self.scout_units.add(scout.tag)
            locs = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            locs.reverse()
            for i, p in enumerate(locs):
                if not self.is_visible(p):
                    self.actions.append(scout.move(p, queue=i > 0))
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
                    self.actions.extend([
                        scout.move(x.position),
                        scout.hold_position(queue=True)
                    ])
                    self.time_table["scout_watchtower"] = self.time

    async def make_overseer(self):
        if not self.units(UnitTypeId.LAIR).exists:
            return
        if self.already_pending(UnitTypeId.OVERSEER, all_units=True) > 0:
            return
        for o in self.units(UnitTypeId.OVERSEER):
            o: Unit = o
            if o.health_percentage > 0.5:
                return
        os = self.units(UnitTypeId.OVERLORD).tags_not_in(self.scout_units)
        if os.exists:
            self.actions.append(os.first(AbilityId.MORPH_OVERSEER))

    @property_cache_once_per_frame
    def need_worker_mineral(self):
        def need_worker_townhall(a: Unit):
            return a.assigned_harvesters < a.ideal_harvesters and \
                   self.visible_enemy_units.closer_than(10, a.position).amount == 0

        t = self.townhalls.ready.filter(need_worker_townhall)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.vespene - self.minerals > 100:
            return False
        if not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if self.minerals - self.vespene > 400:
            return self.count_unit(UnitTypeId.EXTRACTOR) < self.townhalls.ready.amount * 2
        if self.townhalls.ready.amount < 2:
            return False
        if not self.units(UnitTypeId.LAIR).exists:
            return self.count_unit(UnitTypeId.EXTRACTOR) < 1
        if self.townhalls.amount < 4:
            return self.count_unit(UnitTypeId.EXTRACTOR) < self.townhalls.ready.amount * 2 - 2
        else:
            return self.count_unit(UnitTypeId.EXTRACTOR) < self.townhalls.ready.amount * 2

    async def build_spine_crawler(self):
        sc = self.units(UnitTypeId.SPINECRAWLER)
        if sc.exists:
            result = await self.build(UnitTypeId.SPINECRAWLER, sc.furthest_to(self.start_location).position, 4,
                                      placement_step=1)
        elif self.townhalls.exists:
            result = await self.build(UnitTypeId.SPINECRAWLER,
                                      self.townhalls.furthest_to(self.start_location).position.towards(
                                          self.game_info.map_center, 6), 4, placement_step=1)
        else:
            result = await self.build(UnitTypeId.SPINECRAWLER,
                                      self.start_location.towards(self.game_info.map_center, 6), 4,
                                      placement_step=1)
        return result

    def should_expand(self):
        if self.already_pending(UnitTypeId.HATCHERY) > 0:
            return False
        if self.townhalls.amount == 1 and self.supply_used == 14:
            return True
        if self.townhalls.amount < 3:
            return self.units(UnitTypeId.INFESTATIONPIT).ready.exists
        full_workers = True
        total_ideal_harvesters = 0
        for t in self.townhalls.ready:
            t: Unit = t
            full_workers = full_workers and t.surplus_harvesters >= 0
            total_ideal_harvesters += t.ideal_harvesters
        return full_workers and total_ideal_harvesters < 16 * 4

    def enemy_unit_history_count(self, unit_types: List[UnitTypeId]) -> int:
        count = 0
        for unit_type in unit_types:
            if unit_type in self.enemy_unit_history:
                count += len(self.enemy_unit_history[unit_type])
        return count

    def is_location_safe(self, p: Point2):
        return not self.known_enemy_structures.of_type(
            {UnitTypeId.PHOTONCANNON, UnitTypeId.SPINECRAWLER, UnitTypeId.BUNKER}).closer_than(7, p).exists

    @property_cache_once_per_frame
    def est_defense_surplus(self):
        return max(self.est_surplus_forces, self.surplus_forces + self.count_spinecrawler() * 2)

    def count_spinecrawler(self):
        return self.count_unit(UnitTypeId.SPINECRAWLER) + self.count_unit(UnitTypeId.SPINECRAWLERUPROOTED)

    async def defend_early_rush(self) -> bool:
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures. \
            of_type({UnitTypeId.BARRACKS}).closer_than(half_size, self.start_location)
        enemy_units = self.visible_enemy_units.exclude_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        enemy_drones = self.visible_enemy_units.of_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        townhall_to_defend = self.townhalls.ready.furthest_to(self.start_location)
        # build spinecrawlers
        t = max(self.enemy_forces_supply / 3,
                self.known_enemy_structures.of_type({UnitTypeId.WARPGATE, UnitTypeId.BARRACKS}).amount)
        if self.townhalls.ready.amount > 1 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready and \
                self.count_spinecrawler() < min(t, self.townhalls.ready.amount + 1):
            await self.build_spine_crawler()
        if 0 < self.townhalls.ready.amount < 3 and (
                proxy_barracks.exists or
                enemy_units.amount > min(self.count_unit(UnitTypeId.ZERGLING), 5) or
                enemy_drones.amount > 5):
            self.actions.append(self.townhalls.ready.closest_to(self.start_location)(AbilityId.RALLY_HATCHERY_UNITS,
                                                                                     townhall_to_defend.position))
            if self.units(UnitTypeId.ROACHWARREN).ready.exists:
                self.train(UnitTypeId.ROACH)

            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST in abilities:
                    self.actions.append(sp.first(AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST))
                else:
                    self.train(UnitTypeId.ZERGLING)

            else:
                self.train(UnitTypeId.DRONE)

            self.early_attack()

            for t in self.townhalls.ready:
                t: Unit = t
                await self.dist_workers_and_inject_larva(t)

            return True

        return False

    def early_attack(self):
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures. \
            of_type({UnitTypeId.BARRACKS}).closer_than(half_size, self.start_location)

        if self.already_pending(UpgradeId.ZERGLINGMOVEMENTSPEED) > 0 or self.vespene > 100:
            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.empty_workers.closer_than(2.5, a):
                    self.actions.append(w.gather(self.state.mineral_field.closest_to(w)))

        self.drone_gather()

        if self.forces.idle.amount > 20 and len(self.base_trade_units) == 0:
            for f in self.forces.idle.random_group_of(10):
                f: Unit = f
                self.base_trade_units.add(f.tag)
                if proxy_barracks.exists:
                    self.actions.append(
                        f.move(self.start_location.closest(self.far_corners), queue=True))
                if self.enemy_expansions.exists:
                    self.actions.append(
                        f.attack(self.enemy_expansions.closest_to(self.enemy_start_locations[0]).position, queue=True))
                else:
                    self.actions.append(f.attack(self.enemy_start_locations[0], queue=True))


def backwards(f: Point2, t: Point2, distance: Union[float, int]) -> Point2:
    t = f.towards(t, distance)
    return Point2((2 * f.x - t.x, 2 * f.y - t.y))


def has_order(u: Unit, ability_id: AbilityId):
    for o in u.orders:
        o: UnitOrder = o
        if o.ability == ability_id:
            return True
    return False


def count_supply(units: Units) -> float:
    count = 0
    for u in units:
        count += u._type_data._proto.food_required
    return count
