import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from yui_npc_controller.runtime.voice_process import OwnedProcesses, child_environment

class VoiceProcessTests(unittest.TestCase):
    def test_native_children_do_not_inherit_host_credentials(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-key','TUNNEL_TOKEN':'test-only-token','VOICE_TEST_SECRET':'sentinel'}):
            environment=child_environment()
            self.assertNotIn('DEEPSEEK_API_KEY',environment)
            self.assertNotIn('TUNNEL_TOKEN',environment)
            self.assertNotIn('VOICE_TEST_SECRET',environment)
    def test_owned_process_is_stopped_and_no_child_can_start_after_close(self):
        owner=OwnedProcesses()
        process=owner.start([sys.executable,'-c','import time; time.sleep(60)'],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertIsNone(process.poll())
        self.assertEqual(owner.close(),[])
        self.assertIsNotNone(process.poll())
        self.assertEqual(owner.close(),[])
        with self.assertRaises(RuntimeError):owner.start([sys.executable,'-c','pass'])

if __name__=='__main__':unittest.main()
