from socket import timeout
from ding.framework import Task
import logging
import os
import torch

from ding.config import compile_config
from ding.worker import BaseLearner, BattleEpisodeSerialCollector, BattleInteractionSerialEvaluator, NaiveReplayBuffer
from ding.envs import BaseEnvManager
from ding.policy import PPOPolicy
from ding.model import VAC
from ding.utils import set_pkg_seed
from dizoo.league_demo.demo_league import DemoLeague
from dizoo.league_demo.league_demo_ppo_config import league_demo_ppo_config
from ding.utils import DistributedWriter
from ding.framework.middleware import league_learner, league_evaluator, league_dispatcher
from ding.framework.middleware import league_collector, pace_controller, sync_context


def main():
    cfg = compile_config(
        league_demo_ppo_config,
        BaseEnvManager,
        PPOPolicy,
        BaseLearner,
        BattleEpisodeSerialCollector,
        BattleInteractionSerialEvaluator,
        NaiveReplayBuffer,
        save_cfg=True
    )
    set_pkg_seed(0, use_cuda=cfg.policy.cuda)
    tb_logger = DistributedWriter(os.path.join('./{}/log/'.format(cfg.exp_name), 'serial'))
    league = DemoLeague(cfg.policy.other.league)
    policies = {}
    for player_id in league.active_players_ids:
        model = VAC(**cfg.policy.model)
        policy = PPOPolicy(cfg.policy, model=model)
        policies[player_id] = policy

    model = VAC(**cfg.policy.model)
    policy = PPOPolicy(cfg.policy, model=model)
    policies['historical'] = policy

    with Task(async_mode=False) as task:
        if not task.router.is_active:
            logging.info("League should be executed in parallel mode, use `main_league.sh` to execute league!")
            exit(1)
        # League, collect
        task.use(league_dispatcher(task, league=league, is_executor=task.match_labels(["league"])))
        task.use(
            league_collector(
                task, cfg=cfg, tb_logger=tb_logger, policies=policies, is_executor=task.match_labels(["collect"])
            )
        )
        for i, player_id in enumerate(league.active_players_ids):
            is_executor = task.match_labels("learn") and task.router.node_id % len(league.active_players_ids) == i
            task.use(
                league_learner(
                    task, cfg=cfg, tb_logger=tb_logger, player_id=player_id, policies=policies, is_executor=is_executor
                )
            )
        # # Evaluate
        # if task.match_labels(["evaluate"]):
        #     task.use(
        #         league_evaluator(
        #             task, cfg=cfg, tb_logger=tb_logger, player_ids=league.active_players_ids, policies=policies
        #         )
        #     )
        task.run(100)


if __name__ == "__main__":
    main()
