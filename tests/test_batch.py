from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings,connect
from server import Desk


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(self.temp.name);self.settings.initialize()
        self.desk=Desk(self.settings,'test-token')

    def tearDown(self):self.temp.cleanup()

    def test_preview_is_non_mutating_and_excludes_blocked_and_duplicate_destinations(self):
        def detail(id):
            return dict(job=dict(id=id,artist='Artist',album=id,revision='revision-'+id,destination='Artist/Album (2024)',track_count=2),
                        readiness=dict(can_publish=id!='blocked',blockers=[dict(message='Unapproved genre')] if id=='blocked' else []))
        with patch.object(self.desk,'detail',side_effect=detail),patch.object(self.desk,'approve') as approve:
            plan=self.desk.batch_preview('publish',['first','blocked','duplicate'])
        self.assertEqual([j['job_id'] for j in plan['ready']],['first'])
        self.assertEqual(plan['ready'][0]['revision'],'revision-first')
        self.assertEqual(len(plan['blocked']),2);approve.assert_not_called()
        self.assertFalse(self.settings.library.exists())

    def test_delete_uses_individual_file_bound_preview(self):
        def preview(settings,id):
            if id=='active':raise ValueError('Only idle releases can be deleted')
            return dict(job_id=id,album=id,token='file-bound-token',files=3,bytes=123,originals=1)
        with patch('staging.preview_release',side_effect=preview),patch('staging.delete_release') as delete:
            plan=self.desk.batch_preview('delete',['idle','active'])
        self.assertEqual(plan['ready'][0]['token'],'file-bound-token')
        self.assertEqual(plan['blocked'][0]['job_id'],'active');delete.assert_not_called()

    def test_batch_input_boundaries(self):
        for action,ids in [('publish',[]),('publish',['a','a']),('invalid',['a']),('delete',[None]),('publish',[str(i) for i in range(51)])]:
            with self.subTest(action=action,ids=ids),self.assertRaises(ValueError):self.desk.batch_preview(action,ids)

