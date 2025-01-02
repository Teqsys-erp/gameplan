# Copyright (c) 2022, Frappe Technologies Pvt Ltd and contributors
# For license information, please see license.txt

from urllib.parse import urljoin

import frappe
import requests
from bs4 import BeautifulSoup
from frappe.model.document import Document
from pypika.terms import ExistsCriterion

import gameplan
from gameplan.api import invite_by_email
from gameplan.gemoji import get_random_gemoji
from gameplan.mixins.archivable import Archivable
from gameplan.mixins.manage_members import ManageMembersMixin


class GPProject(ManageMembersMixin, Archivable, Document):
	on_delete_cascade = [
		"GP Task",
		"GP Discussion",
		"GP Project Visit",
		"GP Followed Project",
		"GP Page",
		"GP Pinned Project",
	]
	on_delete_set_null = ["GP Notification"]

	@staticmethod
	def get_list_query(query):
		Project = frappe.qb.DocType("GP Project")
		Member = frappe.qb.DocType("GP Member")
		member_exists = (
			frappe.qb.from_(Member)
			.select(Member.name)
			.where(Member.parenttype == "GP Project")
			.where(Member.parent == Project.name)
			.where(Member.user == frappe.session.user)
		)
		query = query.where(
			(Project.is_private == 0) | ((Project.is_private == 1) & ExistsCriterion(member_exists))
		)
		if gameplan.is_guest():
			GuestAccess = frappe.qb.DocType("GP Guest Access")
			project_list = GuestAccess.select(GuestAccess.project).where(
				GuestAccess.user == frappe.session.user
			)
			query = query.where(Project.name.isin(project_list))

		return query

	@staticmethod
	def get_list(query):
		Project = frappe.qb.DocType("GP Project")
		Member = frappe.qb.DocType("GP Member")
		member_exists = (
			frappe.qb.from_(Member)
			.select(Member.name)
			.where(Member.parenttype == "GP Project")
			.where(Member.parent == Project.name)
			.where(Member.user == frappe.session.user)
		)
		query = query.where(
			(Project.is_private == 0) | ((Project.is_private == 1) & ExistsCriterion(member_exists))
		)
		if gameplan.is_guest():
			GuestAccess = frappe.qb.DocType("GP Guest Access")
			project_list = GuestAccess.select(GuestAccess.project).where(
				GuestAccess.user == frappe.session.user
			)
			query = query.where(Project.name.isin(project_list))

		return query

	def as_dict(self, *args, **kwargs) -> dict:
		d = super().as_dict(*args, **kwargs)
		return d

	def before_insert(self):
		if not self.icon:
			self.icon = get_random_gemoji().emoji
		self.append("members", {"user": frappe.session.user})

	def update_progress(self):
		result = frappe.db.get_all(
			"GP Task",
			filters={"project": self.name},
			fields=["sum(is_completed) as completed", "count(name) as total"],
		)[0]
		if result.total > 0:
			self.progress = (result.completed or 0) * 100 / result.total
			self.save()
			self.reload()

	@frappe.whitelist()
	def move_to_team(self, team=None):
		if team is None or self.team == team:
			return
		self.team = team
		self.save()
		for doctype in ["GP Task", "GP Discussion"]:
			for name in frappe.db.get_all(doctype, {"project": self.name}, pluck="name"):
				doc = frappe.get_doc(doctype, name)
				doc.team = self.team
				doc.save()

	@frappe.whitelist()
	def merge_with_project(self, project=None):
		if not project or self.name == project:
			return
		if isinstance(project, str):
			project = int(project)
		if not frappe.db.exists("GP Project", project):
			frappe.throw(f'Invalid Project "{project}"')
		return self.rename(project, merge=True, validate_rename=False, force=True)

	@frappe.whitelist()
	def invite_guest(self, email):
		invite_by_email(email, role="Gameplan Guest", projects=[self.name])

	@frappe.whitelist()
	def remove_guest(self, email):
		name = frappe.db.get_value("GP Guest Access", {"project": self.name, "user": email})
		if name:
			frappe.delete_doc("GP Guest Access", name)

	@frappe.whitelist()
	def track_visit(self):
		if frappe.flags.read_only:
			return

		values = {"user": frappe.session.user, "project": self.name}
		existing = frappe.db.get_value("GP Project Visit", values)
		if existing:
			visit = frappe.get_doc("GP Project Visit", existing)
			visit.last_visit = frappe.utils.now()
			visit.save(ignore_permissions=True)
		else:
			visit = frappe.get_doc(doctype="GP Project Visit")
			visit.update(values)
			visit.last_visit = frappe.utils.now()
			visit.insert(ignore_permissions=True)

	@property
	def is_followed(self):
		return bool(
			frappe.db.exists("GP Followed Project", {"project": self.name, "user": frappe.session.user})
		)

	@frappe.whitelist()
	def follow(self):
		if not self.is_followed:
			frappe.get_doc(doctype="GP Followed Project", project=self.name).insert(ignore_permissions=True)

	@frappe.whitelist()
	def unfollow(self):
		follow_id = frappe.db.get_value(
			"GP Followed Project", {"project": self.name, "user": frappe.session.user}
		)
		frappe.delete_doc("GP Followed Project", follow_id)

	@frappe.whitelist()
	def add_member(self, user):
		if user not in [d.user for d in self.members]:
			self.append("members", {"user": user})
			self.save()

	@frappe.whitelist()
	def join(self):
		self.add_member(frappe.session.user)

	@frappe.whitelist()
	def leave(self):
		user = frappe.session.user
		for member in self.members:
			if member.user == user:
				self.remove(member)
				self.save()
				break

	@frappe.whitelist()
	def update_notification_settings(self, notify_new_posts=0, notify_new_comments=0):
		for member in self.members:
			if member.user == frappe.session.user:
				member.notify_new_posts = notify_new_posts
				member.notify_new_comments = notify_new_comments
				self.save()
				break


def get_meta_tags(url):
	response = requests.get(url, timeout=2, allow_redirects=True)
	soup = BeautifulSoup(response.text, "html.parser")
	title = soup.find("title").text.strip()

	image = None
	favicon = soup.find("link", rel="icon")
	if favicon:
		image = favicon["href"]

	if image and image.startswith("/"):
		image = urljoin(url, image)

	return {"title": title, "image": image}


@frappe.whitelist()
def get_joined_spaces():
	user = frappe.session.user
	projects = frappe.qb.get_query(
		"GP Project",
		filters={"members.user": user},
		fields=["name"],
	).run(as_dict=True, pluck="name")
	guest_access_projects = frappe.qb.get_query(
		"GP Guest Access", filters={"user": user}, fields=["project"]
	).run(as_dict=True, pluck="project")

	return map(str, set(projects + guest_access_projects))
