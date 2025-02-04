import os
import re
import shutil
import decimal

from django.urls.exceptions import NoReverseMatch
from django.urls import reverse

from lava_common.compat import yaml_dump, yaml_load, yaml_safe_load
from lava_results_app.tests.test_names import TestCaseWithFactory
from lava_scheduler_app.models import TestJob, Device
from lava_scheduler_app.utils import mkdir
from lava_results_app.dbutils import (
    map_metadata,
    map_scanned_results,
    create_metadata_store,
    _get_action_metadata,
)
from lava_results_app.models import ActionData, MetaType, TestData, TestCase, TestSuite
from lava_results_app.utils import export_testcase, testcase_export_fields
from lava_dispatcher.parser import JobParser
from lava_dispatcher.device import PipelineDevice
from lava_dispatcher.tests.test_defs import allow_missing_path


class TestMetaTypes(TestCaseWithFactory):
    """
    MetaType and ActionData generation
    """

    def test_job(self):
        MetaType.objects.all().delete()
        TestJob.objects.all().delete()
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        job_def = yaml_safe_load(job.definition)
        job_ctx = job_def.get("context", {})
        job_ctx.update(
            {"no_kvm": True}
        )  # override to allow unit tests on all types of systems
        device = Device.objects.get(hostname="fakeqemu1")
        device_config = device.load_configuration(job_ctx)  # raw dict
        parser = JobParser()
        obj = PipelineDevice(device_config)
        pipeline_job = parser.parse(job.definition, obj, job.id, None, "")
        allow_missing_path(
            pipeline_job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        pipeline = pipeline_job.describe()
        map_metadata(yaml_dump(pipeline), job)
        self.assertEqual(
            MetaType.objects.filter(metatype=MetaType.DEPLOY_TYPE).count(), 1
        )
        self.assertEqual(
            MetaType.objects.filter(metatype=MetaType.BOOT_TYPE).count(), 1
        )
        count = ActionData.objects.all().count()
        self.assertEqual(TestData.objects.all().count(), 1)
        testdata = TestData.objects.all()[0]
        self.assertEqual(testdata.testjob, job)
        for actionlevel in ActionData.objects.all():
            self.assertEqual(actionlevel.testdata, testdata)
        action_levels = []
        action_levels.extend(job.testdata.actionlevels.all())
        self.assertEqual(count, len(action_levels))
        count = ActionData.objects.filter(
            meta_type__metatype=MetaType.DEPLOY_TYPE
        ).count()
        self.assertNotEqual(
            ActionData.objects.filter(meta_type__metatype=MetaType.BOOT_TYPE).count(), 0
        )
        self.assertEqual(
            ActionData.objects.filter(
                meta_type__metatype=MetaType.UNKNOWN_TYPE
            ).count(),
            0,
        )
        for actionlevel in ActionData.objects.filter(
            meta_type__metatype=MetaType.BOOT_TYPE
        ):
            self.assertEqual(actionlevel.testdata.testjob.id, job.id)
        self.assertEqual(
            ActionData.objects.filter(
                meta_type__metatype=MetaType.DEPLOY_TYPE, testdata__testjob=job
            ).count(),
            count,
        )

    def test_export(self):
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        test_suite = TestSuite.objects.get_or_create(name="lava", job=job)[0]
        test_case = TestCase(
            id=1, name="name", suite=test_suite, result=TestCase.RESULT_FAIL
        )
        self.assertTrue(
            any(
                map(
                    lambda v: v in testcase_export_fields(),
                    export_testcase(test_case).keys(),
                )
            )
        )

    def test_duration(self):
        TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        metatype = MetaType(name="fake", metatype=MetaType.DEPLOY_TYPE)
        metatype.save()
        action_data = ActionData(
            meta_type=metatype, action_level="1.2.3", action_name="fake"
        )
        action_data.save()
        action_data.duration = "1.2"
        action_data.save(update_fields=["duration"])
        action_data = ActionData.objects.get(id=action_data.id)  # reload
        self.assertIsInstance(action_data.duration, decimal.Decimal)
        # unit tests check the instance as well as the value.
        self.assertEqual(float(action_data.duration), 1.2)
        action_data.timeout = 300
        action_data.save(update_fields=["timeout"])
        self.assertEqual(action_data.timeout, 300)

    def test_decimal_yaml_dump(self):
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        test_dict = {
            "definition": "unit-test",
            "case": "unit-test",
            "measurement": decimal.Decimal(1234.5),
            "result": "pass",
        }
        test_case = map_scanned_results(test_dict, job, {}, None)
        self.assertEqual(yaml_load(test_case.metadata)["measurement"], "1234.5")

    def test_case_as_url(self):
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        test_dict = {
            "definition": "unit-test",
            "case": "unit-test",
            "level": "1.3.4.1",
            # list of numbers, generates a much longer YAML string than just the count
            "result": "pass",
        }
        pattern = "[-_a-zA-Z0-9.\\(\\)]+"
        matches = re.search(pattern, test_dict["case"])
        self.assertIsNotNone(matches)  # passes
        self.assertEqual(matches.group(0), test_dict["case"])
        suite, _ = TestSuite.objects.get_or_create(
            name=test_dict["definition"], job=job
        )
        case, _ = TestCase.objects.get_or_create(
            suite=suite, name=test_dict["case"], result=TestCase.RESULT_PASS
        )
        self.assertIsNotNone(reverse("lava.results.testcase", args=[case.id]))
        self.assertIsNotNone(
            reverse("lava.results.testcase", args=[job.id, suite.name, case.id])
        )
        self.assertIsNotNone(map_scanned_results(test_dict, job, {}, None))
        # now break the reverse pattern
        test_dict["case"] = "unit test"  # whitespace in the case name
        matches = re.search(pattern, test_dict["case"])
        self.assertIsNotNone(matches)
        self.assertRaises(
            NoReverseMatch,
            reverse,
            "lava.results.testcase",
            args=[job.id, suite.name, test_dict["case"]],
        )

    def test_metastore(self):
        field = TestCase._meta.get_field("metadata")
        level = "1.3.5.1"
        # artificially inflate results to represent a set of kernel messages
        results = {
            "definition": "lava",
            "case": "unit-test",
            "level": level,
            # list of numbers, generates a much longer YAML string than just the count
            "extra": range(int(field.max_length / 2)),
            "result": "pass",
        }
        stub = "%s-%s-%s.yaml" % (results["definition"], results["case"], level)
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        meta_filename = os.path.join(job.output_dir, "metadata", stub)
        filename = "%s/job-%s/pipeline/%s/%s-%s.yaml" % (
            job.output_dir,
            job.id,
            level.split(".")[0],
            level,
            results["definition"],
        )

        mkdir(os.path.dirname(filename))
        if os.path.exists(meta_filename):
            # isolate from other unit tests
            os.unlink(meta_filename)
        self.assertEqual(meta_filename, create_metadata_store(results, job))
        ret = map_scanned_results(results, job, {}, meta_filename)
        self.assertIsNotNone(ret)
        ret.save()
        self.assertEqual(TestCase.objects.filter(name="unit-test").count(), 1)
        test_data = yaml_load(TestCase.objects.filter(name="unit-test")[0].metadata)
        self.assertEqual(test_data["extra"], meta_filename)
        self.assertTrue(os.path.exists(meta_filename))
        with open(test_data["extra"], "r") as extra_file:
            data = yaml_load(extra_file)
        self.assertIsNotNone(data)
        os.unlink(meta_filename)
        shutil.rmtree(job.output_dir)

    def test_repositories(self):
        job = TestJob.from_yaml_and_user(self.factory.make_job_yaml(), self.user)
        job_def = yaml_safe_load(job.definition)
        job_ctx = job_def.get("context", {})
        job_ctx.update(
            {"no_kvm": True}
        )  # override to allow unit tests on all types of systems
        device = Device.objects.get(hostname="fakeqemu1")
        device_config = device.load_configuration(job_ctx)  # raw dict
        parser = JobParser()
        obj = PipelineDevice(device_config)
        pipeline_job = parser.parse(job.definition, obj, job.id, None, "")
        allow_missing_path(
            pipeline_job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        pipeline = pipeline_job.describe()
        testdata, _ = TestData.objects.get_or_create(testjob=job)
        retval = _get_action_metadata(pipeline["job"]["actions"])
        self.assertEqual(
            retval,
            {
                "test.1.common.definition.from": "git",
                "test.0.common.definition.repository": "git://git.linaro.org/lava-team/lava-functional-tests.git",
                "test.0.common.definition.name": "smoke-tests",
                "test.1.common.definition.repository": "http://git.linaro.org/lava-team/lava-functional-tests.git",
                "boot.0.common.method": "qemu",
                "test.1.common.definition.name": "singlenode-advanced",
                "test.0.common.definition.from": "git",
                "test.0.common.definition.path": "lava-test-shell/smoke-tests-basic.yaml",
                "test.1.common.definition.path": "lava-test-shell/single-node/singlenode03.yaml",
            },
        )

    def test_parameter_support(self):
        data = self.factory.make_job_data()
        test_block = [block for block in data["actions"] if "test" in block][0]
        smoke = test_block["test"]["definitions"][0]
        smoke["parameters"] = {
            "VARIABLE_NAME_1": "first variable value",
            "VARIABLE_NAME_2": "second value",
        }
        job = TestJob.from_yaml_and_user(yaml_dump(data), self.user)
        job_def = yaml_safe_load(job.definition)
        job_ctx = job_def.get("context", {})
        job_ctx.update(
            {"no_kvm": True}
        )  # override to allow unit tests on all types of systems
        device = Device.objects.get(hostname="fakeqemu1")
        device_config = device.load_configuration(job_ctx)  # raw dict
        parser = JobParser()
        obj = PipelineDevice(device_config)
        pipeline_job = parser.parse(job.definition, obj, job.id, None, "")
        allow_missing_path(
            pipeline_job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        pipeline = pipeline_job.describe()
        testdata, _ = TestData.objects.get_or_create(testjob=job)
        retval = _get_action_metadata(pipeline["job"]["actions"])
        self.assertIn("test.0.common.definition.parameters.VARIABLE_NAME_2", retval)
        self.assertIn("test.0.common.definition.parameters.VARIABLE_NAME_1", retval)
        self.assertEqual(
            retval["test.0.common.definition.parameters.VARIABLE_NAME_1"],
            "first variable value",
        )
        self.assertEqual(
            retval["test.0.common.definition.parameters.VARIABLE_NAME_2"],
            "second value",
        )

    def test_job_multi(self):
        MetaType.objects.all().delete()
        multi_test_file = os.path.join(os.path.dirname(__file__), "multi-test.yaml")
        self.assertTrue(os.path.exists(multi_test_file))
        with open(multi_test_file, "r") as test_support:
            data = test_support.read()
        job = TestJob.from_yaml_and_user(data, self.user)
        job_def = yaml_safe_load(job.definition)
        job_ctx = job_def.get("context", {})
        job_ctx.update(
            {"no_kvm": True}
        )  # override to allow unit tests on all types of systems
        device = Device.objects.get(hostname="fakeqemu1")
        device_config = device.load_configuration(job_ctx)  # raw dict
        parser = JobParser()
        obj = PipelineDevice(device_config)
        pipeline_job = parser.parse(job.definition, obj, job.id, None, "")
        allow_missing_path(
            pipeline_job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        pipeline = pipeline_job.describe()
        map_metadata(yaml_dump(pipeline), job)

    def test_inline(self):
        """
        Test inline can be parsed without run steps
        """
        data = self.factory.make_job_data()
        test_block = [block for block in data["actions"] if "test" in block][0]
        smoke = [
            {
                "path": "inline/smoke-tests-basic.yaml",
                "from": "inline",
                "name": "smoke-tests-inline",
                "repository": {
                    "install": {"steps": ["apt"]},
                    "metadata": {
                        "description": "Basic system test command for Linaro Ubuntu images",
                        "format": "Lava-Test Test Definition 1.0",
                        "name": "smoke-tests-basic",
                    },
                },
            }
        ]
        test_block["test"]["definitions"] = smoke
        job = TestJob.from_yaml_and_user(yaml_dump(data), self.user)
        job_def = yaml_safe_load(job.definition)
        job_ctx = job_def.get("context", {})
        job_ctx.update(
            {"no_kvm": True}
        )  # override to allow unit tests on all types of systems
        device = Device.objects.get(hostname="fakeqemu1")
        device_config = device.load_configuration(job_ctx)  # raw dict
        parser = JobParser()
        obj = PipelineDevice(device_config)
        pipeline_job = parser.parse(job.definition, obj, job.id, None, "")
        allow_missing_path(
            pipeline_job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        pipeline = pipeline_job.describe()
        map_metadata(yaml_dump(pipeline), job)
