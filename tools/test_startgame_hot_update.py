"""Static transition regression: python -m unittest discover -s tools -p test_startgame_hot_update.py"""
import json
from pathlib import Path
import unittest


class HotUpdateTransitions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'assets/resource/base/pipeline/StartGame.json'
        cls.nodes = json.loads(path.read_text(encoding='utf-8'))

    def test_running_app_can_resume_hot_update(self):
        following = self.nodes['StartGame_Check_App_Alive']['next']
        for node in ('StartGame_Update_Data', 'StartGame_Update_Data_DownLoading', 'StartGame_Loading'):
            self.assertLess(following.index(node), following.index('Global_Null'))

    def test_download_can_finish_at_loading_screen(self):
        for node in ('StartGame_Update_Data_Click', 'StartGame_Update_Data_DownLoading'):
            self.assertIn('StartGame_Loading', self.nodes[node]['next'])
            self.assertIn('StartGame_TouchStart', self.nodes[node]['next'])

    def test_download_timeout_does_not_request_apk_update(self):
        self.assertEqual(['StartGame_LoadingPage'], self.nodes['StartGame_Update_Data_DownLoading']['on_error'])
        self.assertEqual(['StartGame_UpdateCloseGame'], self.nodes['StartGame_Update_PGM_Click']['next'])

    def test_existing_download_confirmation_is_unchanged(self):
        self.assertEqual(['将下载游戏'], self.nodes['StartGame_Update_Data']['expected'])
        self.assertEqual(['下载'], self.nodes['StartGame_Update_Data_Click']['expected'])
        self.assertEqual('Click', self.nodes['StartGame_Update_Data_Click']['action'])


if __name__ == '__main__':
    unittest.main()
